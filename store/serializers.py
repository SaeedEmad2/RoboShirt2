from decimal import Decimal
from django.db import transaction
from rest_framework import serializers
from .signals import order_created
from .models import Cart, CartItem, Customer, Order, OrderItem, Product, Collection, Review, Payment


class CollectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Collection
        fields = ['id', 'title', 'products_count']

    products_count = serializers.IntegerField(read_only=True)


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ['id', 'title', 'description', 'slug', 'inventory',
                  'unit_price', 'price_with_tax', 'collection']

    price_with_tax = serializers.SerializerMethodField(
        method_name='calculate_tax')

    def calculate_tax(self, product: Product):
        return product.unit_price * Decimal(1.1)


class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ['id', 'date', 'name', 'description']

    def create(self, validated_data):
        product_id = self.context['product_id']
        return Review.objects.create(product_id=product_id, **validated_data)


class SimpleProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ['id', 'title', 'unit_price']


class CartItemSerializer(serializers.ModelSerializer):
    product = SimpleProductSerializer()
    total_price = serializers.SerializerMethodField()

    def get_total_price(self, cart_item: CartItem):
        return cart_item.quantity * cart_item.product.unit_price

    class Meta:
        model = CartItem
        fields = ['id', 'product', 'quantity', 'total_price']


class CartSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    items = CartItemSerializer(many=True, read_only=True)
    total_price = serializers.SerializerMethodField()

    def get_total_price(self, cart):
        return sum([item.quantity * item.product.unit_price for item in cart.items.all()])

    class Meta:
        model = Cart
        fields = ['id', 'items', 'total_price']


class AddCartItemSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField()

    def validate_product_id(self, value):
        if not Product.objects.filter(pk=value).exists():
            raise serializers.ValidationError(
                'No product with the given ID was found.')
        return value

    def save(self, **kwargs):
        cart_id = self.context['cart_id']
        product_id = self.validated_data['product_id']
        quantity = self.validated_data['quantity']

        try:
            cart_item = CartItem.objects.get(
                cart_id=cart_id, product_id=product_id)
            cart_item.quantity += quantity
            cart_item.save()
            self.instance = cart_item
        except CartItem.DoesNotExist:
            self.instance = CartItem.objects.create(
                cart_id=cart_id, **self.validated_data)

        return self.instance

    class Meta:
        model = CartItem
        fields = ['id', 'product_id', 'quantity']


class UpdateCartItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = CartItem
        fields = ['quantity']


class CustomerSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Customer
        fields = ['id', 'user_id', 'phone', 'birth_date', 'membership']


class OrderItemSerializer(serializers.ModelSerializer):
    product = SimpleProductSerializer()

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'unit_price', 'quantity']


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)

    class Meta:
        model = Order
        fields = ['id', 'customer', 'placed_at', 'payment_status', 'items']


class UpdateOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = ['payment_status']


class CreateOrderSerializer(serializers.Serializer):
    cart_id = serializers.UUIDField()

    def validate_cart_id(self, cart_id):
        if not Cart.objects.filter(pk=cart_id).exists():
            raise serializers.ValidationError(
                'No cart with the given ID was found.')
        if CartItem.objects.filter(cart_id=cart_id).count() == 0:
            raise serializers.ValidationError('The cart is empty.')
        return cart_id

    def save(self, **kwargs):
        with transaction.atomic():
            cart_id = self.validated_data['cart_id']

            customer = Customer.objects.get(
                user_id=self.context['user_id'])
            order = Order.objects.create(customer=customer)

            cart_items = CartItem.objects \
                .select_related('product') \
                .filter(cart_id=cart_id)
            order_items = [
                OrderItem(
                    order=order,
                    product=item.product,
                    unit_price=item.product.unit_price,
                    quantity=item.quantity
                ) for item in cart_items
            ]
            OrderItem.objects.bulk_create(order_items)

            Cart.objects.filter(pk=cart_id).delete()

            order_created.send_robust(self.__class__, order=order)

            return order

class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ['id', 'payment_method', 'order', 'status', 'amount', 'payment_date', 'transaction_id', 'receipt_id']
        read_only_fields = ['transaction_id', 'receipt_id', 'status', 'payment_date']

class PaymentInitiateSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    payment_method = serializers.ChoiceField(choices=Payment.PAYMENT_CHOICES)
    card_number = serializers.CharField(max_length=16, min_length=16, required=False)
    expiry_month = serializers.CharField(max_length=2, required=False)
    expiry_year = serializers.CharField(max_length=2, required=False)
    cvv = serializers.CharField(max_length=3, min_length=3, required=False)
    
    def validate(self, data):
        order_id = data.get('order_id')
        payment_method = data.get('payment_method')
        
        try:
            order = Order.objects.get(id=order_id)
        except Order.DoesNotExist:
            raise serializers.ValidationError(f"Order with ID {order_id} does not exist")
        
        # Ensure the order belongs to the current user
        if order.customer.user != self.context['request'].user:
            raise serializers.ValidationError("You do not have permission to pay for this order.")
        
        # If payment method is credit card, validate card details
        if payment_method == 'credit_card':
            required_fields = ['card_number', 'expiry_month', 'expiry_year', 'cvv']
            for field in required_fields:
                if not data.get(field):
                    raise serializers.ValidationError(f"{field} is required for credit card payments")
        
        return data

class PaymentVerifySerializer(serializers.Serializer):
    transaction_id = serializers.CharField()

class PaymentReceiptSerializer(serializers.ModelSerializer):
    customer_name = serializers.SerializerMethodField()
    order_number = serializers.SerializerMethodField()
    
    def get_customer_name(self, obj):
        user = obj.customer.user
        return f"{user.first_name} {user.last_name}" if user.first_name else user.username
    
    def get_order_number(self, obj):
        return obj.order.id
    
    class Meta:
        model = Payment
        fields = ['id', 'receipt_id', 'transaction_id', 'payment_method', 'amount', 
                  'payment_date', 'status', 'customer_name', 'order_number']