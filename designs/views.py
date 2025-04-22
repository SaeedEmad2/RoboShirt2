from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.viewsets import ModelViewSet  # Import ModelViewSet
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly, AllowAny
from rest_framework.response import Response
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import os
import requests
import base64
from django.core.files.base import ContentFile
from django.conf import settings
from .models import Design, Template, Mockup
from .serializers import DesignSerializer, TemplateSerializer, MockupSerializer, MockupPreviewSerializer
from store.models import Customer
from rest_framework.views import APIView

# Design ViewSet
class DesignViewSet(viewsets.ModelViewSet):
    serializer_class = DesignSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Return designs only for the logged-in user
        customer = Customer.objects.filter(user=self.request.user).first()
        if customer:
            return Design.objects.filter(customer=customer)
        return Design.objects.none()

    def perform_create(self, serializer):
        # Automatically set the customer based on the logged-in user
        customer = Customer.objects.filter(user=self.request.user).first()
        if not customer:
            raise ValidationError("User does not have an associated customer account")
        serializer.save(customer=customer)

    @action(detail=True, methods=['delete'], url_path='delete')
    def custom_delete(self, request, pk=None):
        design = self.get_object()
        design.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# Template ViewSet
class TemplateViewSet(ModelViewSet):
    queryset = Template.objects.all()
    serializer_class = TemplateSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['category']  # Filter by category field
    search_fields = ['category']  # Search by category name


# Mockup ViewSet
class MockupViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MockupSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        customer = Customer.objects.filter(user=self.request.user).first()
        if customer:
            return Mockup.objects.filter(design__customer=customer)
        return Mockup.objects.none()

    @action(detail=False, methods=['post'], url_path='preview')
    def preview(self, request):
        serializer = MockupPreviewSerializer(data=request.data)
        if serializer.is_valid():
            design_id = serializer.validated_data['design_id']
            color = serializer.validated_data['color']
            size = serializer.validated_data['size']

            design = get_object_or_404(Design, id=design_id)
            customer = Customer.objects.filter(user=request.user).first()

            if not customer or design.customer != customer:
                return Response(
                    {"error": "You don't have permission to access this design"},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Try to get existing mockup or generate a new one
            mockup, created = Mockup.objects.get_or_create(
                design=design, color=color, size=size,
                defaults={'mockup_image': generate_mockup(design, color, size)}
            )

            return Response(MockupSerializer(mockup).data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Helper Function to Generate Mockups
def generate_mockup(design, color, size):
    """Generate a mockup image by overlaying the design on a t-shirt template."""
    tshirt_template_path = Path(settings.BASE_DIR) / 'static' / 'tshirt_templates' / f'{color}.png'

    # Use default white template if the specified color template doesn't exist
    if not tshirt_template_path.exists():
        tshirt_template_path = Path(settings.BASE_DIR) / 'static' / 'tshirt_templates' / 'default.png'

    try:
        tshirt = Image.open(tshirt_template_path)
    except FileNotFoundError:
        tshirt = Image.new('RGB', (800, 800), color)

    if design.design_file:
        try:
            design_image = Image.open(design.design_file.path)
            size_factor = {'xs': 0.5, 's': 0.6, 'm': 0.7, 'l': 0.8, 'xl': 0.9, 'xxl': 1.0}.get(size, 0.7)
            new_width, new_height = int(design_image.width * size_factor), int(design_image.height * size_factor)
            design_image = design_image.resize((new_width, new_height))

            position = ((tshirt.width - new_width) // 2, (tshirt.height - new_height) // 3)
            if design_image.mode == 'RGBA':
                tshirt.paste(design_image, position, design_image)
            else:
                tshirt.paste(design_image, position)
        except Exception:
            draw = ImageDraw.Draw(tshirt)
            font = ImageFont.load_default()
            draw.text((400, 400), design.design_description, fill="black", font=font)
    else:
        draw = ImageDraw.Draw(tshirt)
        font = ImageFont.load_default()
        draw.text((400, 400), design.design_description, fill="black", font=font)

    image_io = BytesIO()
    tshirt.save(image_io, format='PNG')

    mockup = Mockup(design=design, color=color, size=size)
    mockup.mockup_image.save(f'mockup_{design.id}_{color}_{size}.png', ContentFile(image_io.getvalue()))
    mockup.save()

    return mockup


import logging

logger = logging.getLogger(__name__)

class GenerateImageView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        # Check if an audio file is provided
        audio_file = request.FILES.get("audio")
        if audio_file:
            # LemonFox Whisper API details
            whisper_api_url = "https://api.lemonfox.ai/v1/audio/transcriptions"
            whisper_api_key = os.getenv("LEMONFOX_API_KEY")  # Store your API key in an environment variable

            if not whisper_api_key:
                logger.error("LemonFox API key is not set. Please configure the LEMONFOX_API_KEY environment variable.")
                return Response({"error": "Server configuration error"}, status=500)

            # Prepare headers and data for the Whisper API request
            headers = {
                "Authorization": f"Bearer {whisper_api_key}"
            }
            files = {
                "file": audio_file
            }
            data = {
                "language": "arabic",  # Specify the language
                "response_format": "json",  # Get the response in JSON format
                "translate": True  # Enable translation to English
            }

            # Send the request to the Whisper API
            
            whisper_response = requests.post(whisper_api_url, headers=headers, files=files, data=data)
            print(whisper_response.text)
            if whisper_response.status_code == 200:
                # Extract the transcribed text from the response
                prompt = whisper_response.json().get("text", "").strip()
                if not prompt:
                    return Response({"error": "Failed to transcribe audio"}, status=400)
            else:
                logger.error(f"Whisper API error: {whisper_response.status_code}, {whisper_response.text}")
                return Response({"error": "Failed to process audio file"}, status=whisper_response.status_code)
        else:
            # Get the prompt from the request if no audio file is provided
            prompt = request.data.get("prompt")
            if not prompt:
                return Response({"error": "Prompt or audio file is required"}, status=400)

        # Optional parameters
        aspect_ratio = request.data.get("aspect_ratio", "1:1")
        output_format = request.data.get("output_format", "jpeg")

        # Stable Diffusion API details
        api_url = "https://api.stability.ai/v2beta/stable-image/generate/sd3"
        api_key = os.getenv("STABLE_DIFFUSION_API_KEY")

        # Prepare headers
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

        # Prepare data for the request
        files = {
            "prompt": (None, prompt),
            "aspect_ratio": (None, aspect_ratio),
            "output_format": (None, output_format),
        }

        # Log the request
        logger.info(f"Sending request to Stable Diffusion API: {files}")

        # Send the request to the Stable Diffusion API
        response = requests.post(api_url, headers=headers, files=files)

        # Log the response
        logger.info(f"Response from Stable Diffusion API: {response.status_code}, {response.text}")

        if response.status_code == 200:
            # Parse the JSON response
            response_json = response.json()

            # Get the base64-encoded image string
            base64_image = response_json.get("image")
            if not base64_image:
                return Response({"error": "Image data not found in response"}, status=500)

               # Decode the base64 string

            try:

                image_data = base64.b64decode(base64_image)

                # Save the image to a file

                image_path = os.path.join(settings.MEDIA_ROOT, "generated_image.jpeg")

                with open(image_path, "wb") as image_file:

                    image_file.write(image_data)



                logger.info(f"Image saved as '{image_path}'")

                return Response({"message": "Image generated successfully", "image_path": image_path}, status=200)

            except Exception as e:

                logger.error(f"Error decoding or saving image: {e}")

                return Response({"error": "Failed to decode or save image"}, status=500)

