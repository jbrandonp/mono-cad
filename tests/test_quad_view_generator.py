from PIL import Image
import io
import build123d as bd

from mono.vision.quad_view_generator import QuadViewGenerator, QuadViewResult


def test_quad_view_generator_box() -> None:
    """Vérifie la génération d'une planche composite 4 vues 1024x1024 pour un solide."""
    box = bd.Box(60.0, 40.0, 20.0)
    generator = QuadViewGenerator(size=1024)

    result = generator.generate(box, title="Test Box Quad View")

    assert isinstance(result, QuadViewResult)
    assert len(result.image_bytes) > 5000  # Image PNG non vide
    assert result.image_bytes[:8] == b"\x89PNG\r\n\x1a\n"

    # Vérification des dimensions de l'image
    img = Image.open(io.BytesIO(result.image_bytes))
    assert img.size == (1024, 1024)
    assert img.format == "PNG"

    # Vérification des métadonnées
    assert abs(result.bbox_dims[0] - 60.0) < 1e-3
    assert abs(result.bbox_dims[1] - 40.0) < 1e-3
    assert abs(result.bbox_dims[2] - 20.0) < 1e-3
    assert abs(result.volume_mm3 - 48000.0) < 1.0


def test_quad_view_generator_fillet_part() -> None:
    """Vérifie la génération sur une pièce mécanique avec cylindre et congés."""
    with bd.BuildPart() as p:
        bd.Box(80.0, 50.0, 25.0)
        bd.Cylinder(radius=10.0, height=30.0, mode=bd.Mode.SUBTRACT)

    generator = QuadViewGenerator(size=1024)
    result = generator.generate(p.part, title="Part with Hole")

    assert len(result.image_bytes) > 5000
    img = Image.open(io.BytesIO(result.image_bytes))
    assert img.size == (1024, 1024)
