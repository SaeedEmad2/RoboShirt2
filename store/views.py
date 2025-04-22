from store.permissions import FullDjangoModelPermissions, IsAdminOrReadOnly, ViewCustomerHistoryPermission
from store.pagination import DefaultPagination
from django.db.models.aggregates import Count
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.decorators import action, permission_classes
from rest_framework.generics import RetrieveAPIView
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.mixins import CreateModelMixin, DestroyModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.permissions import AllowAny, DjangoModelPermissions, DjangoModelPermissionsOrAnonReadOnly, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, GenericViewSet
from rest_framework import mixins
from rest_framework import status
import uuid
import random
from .filters import ProductFilter
from .models import Cart, CartItem, Collection, Customer, Order, OrderItem, Product, Review, Payment
from .serializers import (AddCartItemSerializer,
                          CartItemSerializer,
                          CartSerializer, CollectionSerializer,
                          CreateOrderSerializer, CustomerSerializer, 
                          OrderSerializer, ProductSerializer, ReviewSerializer,
                          UpdateCartItemSerializer, UpdateOrderSerializer,
                          PaymentSerializer, PaymentInitiateSerializer,
                          PaymentVerifySerializer,PaymentReceiptSerializer,)


class ProductViewSet(ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ProductFilter
    pagination_class = DefaultPagination
    permission_classes = [IsAdminOrReadOnly]
    search_fields = ['title', 'description']
    ordering_fields = ['unit_price', 'last_update']

    def get_serializer_context(self):
        return {'request': self.request}

    def destroy(self, request, *args, **kwargs):
        if OrderItem.objects.filter(product_id=kwargs['pk']).count() > 0:
            return Response({'error': 'Product cannot be deleted because it is associated with an order item.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

        return super().destroy(request, *args, **kwargs)


class CollectionViewSet(ModelViewSet):
    queryset = Collection.objects.annotate(
        products_count=Count('products')).all()
    serializer_class = CollectionSerializer
    permission_classes = [IsAdminOrReadOnly]

    def destroy(self, request, *args, **kwargs):
        if Product.objects.filter(collection_id=kwargs['pk']):
            return Response({'error': 'Collection cannot be deleted because it includes one or more products.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

        return super().destroy(request, *args, **kwargs)


class ReviewViewSet(ModelViewSet):
    serializer_class = ReviewSerializer

    def get_queryset(self):
        return Review.objects.filter(product_id=self.kwargs['product_pk'])

    def get_serializer_context(self):
        return {'product_id': self.kwargs['product_pk']}


class CartViewSet(CreateModelMixin,
                  RetrieveModelMixin,
                  DestroyModelMixin,
                  GenericViewSet):
    queryset = Cart.objects.prefetch_related('items__product').all()
    serializer_class = CartSerializer


class CartItemViewSet(ModelViewSet):
    http_method_names = ['get', 'post', 'patch', 'delete']

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return AddCartItemSerializer
        elif self.request.method == 'PATCH':
            return UpdateCartItemSerializer
        return CartItemSerializer

    def get_serializer_context(self):
        return {'cart_id': self.kwargs['cart_pk']}

    def get_queryset(self):
        return CartItem.objects \
            .filter(cart_id=self.kwargs['cart_pk']) \
            .select_related('product')


class CustomerViewSet(ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    permission_classes = [IsAdminUser]

    @action(detail=True, permission_classes=[ViewCustomerHistoryPermission])
    def history(self, request, pk):
        return Response('ok')

    @action(detail=False, methods=['GET', 'PUT'], permission_classes=[IsAuthenticated])
    def me(self, request):
        customer = Customer.objects.get(
            user_id=request.user.id)
        if request.method == 'GET':
            serializer = CustomerSerializer(customer)
            return Response(serializer.data)
        elif request.method == 'PUT':
            serializer = CustomerSerializer(customer, data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)


class OrderViewSet(ModelViewSet):
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_permissions(self):
        if self.request.method in ['PATCH', 'DELETE']:
            return [IsAdminUser()]
        return [IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        serializer = CreateOrderSerializer(
            data=request.data,
            context={'user_id': self.request.user.id})
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        serializer = OrderSerializer(order)
        return Response(serializer.data)

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return CreateOrderSerializer
        elif self.request.method == 'PATCH':
            return UpdateOrderSerializer
        return OrderSerializer

    def get_queryset(self):
        user = self.request.user

        if user.is_staff:
            return Order.objects.all()

        customer_id = Customer.objects.only(
            'id').get(user_id=user.id)
        return Order.objects.filter(customer_id=customer_id)
    
    
class PaymentViewSet(mixins.RetrieveModelMixin, mixins.ListModelMixin, GenericViewSet):
    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Payment.objects.filter(customer__user=self.request.user)

    @action(detail=False, methods=['post'], url_path='initiate')
    def initiate_payment(self, request):
        serializer = PaymentInitiateSerializer(data=request.data)
        if serializer.is_valid():
            order_id = serializer.validated_data['order_id']
            payment_method = serializer.validated_data['payment_method']

            # Get the order
            order = Order.objects.get(id=order_id)

            # Check that the order belongs to the user
            if order.customer.user != request.user:
                return Response(
                    {"error": "You don't have permission to pay for this order"},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Generate a unique transaction ID
            transaction_id = f"TXN-{uuid.uuid4().hex[:12].upper()}"

            # Create a masked card detail if using credit card
            card_details = None
            if payment_method == 'credit_card':
                card_number = serializer.validated_data.get('card_number')
                card_details = {
                    'card_number': f"XXXX-XXXX-XXXX-{card_number[-4:]}",
                    'expiry': f"{serializer.validated_data.get('expiry_month')}/{serializer.validated_data.get('expiry_year')}"
                }

            # Create payment record with 'processing' status
            payment = Payment.objects.create(
                payment_method=payment_method,
                order=order,
                user=request.user,
                status='processing',
                amount=order.total_price,
                transaction_id=transaction_id,
                card_details=card_details
            )

            # Simulate payment processing (for mock API)
            success = random.random() < 0.8

            if success:
                # Generate a receipt ID
                receipt_id = f"RCPT-{uuid.uuid4().hex[:10].upper()}"

                # Update payment status and add receipt
                payment.status = 'completed'
                payment.receipt_id = receipt_id
                payment.save()

                # Update order status
                order.status = 'processing'
                order.save()

                return Response({
                    "status": "success",
                    "message": "Payment processed successfully",
                    "transaction_id": transaction_id,
                    "receipt_id": receipt_id
                }, status=status.HTTP_200_OK)
            else:
                # Simulate payment failure
                payment.status = 'failed'
                payment.save()

                return Response({
                    "status": "failed",
                    "message": "Payment failed. Please try again.",
                    "transaction_id": transaction_id,
                    "error_code": "CARD_DECLINED"
                }, status=status.HTTP_400_BAD_REQUEST)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='verify')
    def verify_payment(self, request):
        serializer = PaymentVerifySerializer(data=request.data)
        if serializer.is_valid():
            transaction_id = serializer.validated_data['transaction_id']

            try:
                payment = Payment.objects.get(transaction_id=transaction_id, user=request.user)

                return Response({
                    "status": payment.status,
                    "message": f"Payment status: {payment.status}",
                    "transaction_id": transaction_id,
                    "receipt_id": payment.receipt_id if payment.status == 'completed' else None
                }, status=status.HTTP_200_OK)

            except Payment.DoesNotExist:
                return Response({
                    "error": "Transaction not found"
                }, status=status.HTTP_404_NOT_FOUND)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PaymentReceiptView(RetrieveAPIView):
    serializer_class = PaymentReceiptSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'receipt_id'

    def get_queryset(self):
        return Payment.objects.filter(
            user=self.request.user,
            status='completed'
        )
