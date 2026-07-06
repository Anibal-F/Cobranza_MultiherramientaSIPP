import os
import shutil

import pdfplumber
import pytesseract
from PIL import Image

EXTENSIONES_IMAGEN = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

# Resolución de rasterizado para hacer OCR de PDFs sin capa de texto.
_OCR_DPI = 300


def _configurar_tesseract_windows() -> None:
    if os.name != "nt" or shutil.which("tesseract"):
        return

    candidatos = [
        os.environ.get("TESSERACT_CMD", ""),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for ruta in candidatos:
        if ruta and os.path.exists(ruta):
            pytesseract.pytesseract.tesseract_cmd = ruta
            tessdata = os.path.join(os.path.dirname(ruta), "tessdata")
            if os.path.isdir(tessdata) and not os.environ.get("TESSDATA_PREFIX"):
                os.environ["TESSDATA_PREFIX"] = tessdata
            return


_configurar_tesseract_windows()


def _ocr_imagen(img: Image.Image) -> str:
    # PSM 6 ("assume single uniform block") lee label+valor en la misma línea
    # en tablas de dos columnas (comprobantes bancarios SPEI/Santander/BBVA),
    # evitando que la columna de labels se mezcle con la de valores.
    try:
        return pytesseract.image_to_string(img, lang="spa+eng", config="--psm 6").strip()
    except pytesseract.TesseractError:
        return pytesseract.image_to_string(img, config="--psm 6").strip()


def _extraer_texto_pdf(ruta: str) -> str:
    with pdfplumber.open(ruta) as pdf:
        partes = []
        for pagina in pdf.pages:
            texto = pagina.extract_text()
            if texto:
                partes.append(texto)
        unido = "\n".join(partes).strip()
        if unido:
            return unido
        # PDF sin capa de texto (comprobante renderizado como imagen, p. ej.
        # Santander "Resumen de su Operación"): rasterizamos cada página y la
        # pasamos por OCR como si fuera un adjunto de imagen.
        ocr = []
        for pagina in pdf.pages:
            imagen = pagina.to_image(resolution=_OCR_DPI).original
            texto = _ocr_imagen(imagen)
            if texto:
                ocr.append(texto)
        return "\n".join(ocr).strip()


def _extraer_texto_imagen(ruta: str) -> str:
    with Image.open(ruta) as img:
        return _ocr_imagen(img)


def extraer_texto_adjunto(ruta: str) -> str:
    """Extrae el texto de un comprobante adjunto: texto real si es PDF, OCR si
    es imagen. Si el PDF no trae capa de texto, cae a OCR de la página. Regresa
    cadena vacía si el formato no es soportado o falla."""
    extension = os.path.splitext(ruta)[1].lower()
    try:
        if extension == ".pdf":
            return _extraer_texto_pdf(ruta)
        if extension in EXTENSIONES_IMAGEN:
            return _extraer_texto_imagen(ruta)
    except Exception:
        return ""
    return ""
