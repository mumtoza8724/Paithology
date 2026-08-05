import os
import qrcode


def create_qr(text, filename, folder="static/qr"):
    """
    Создает QR-код и возвращает путь к сохраненному файлу.

    text - информация, которая будет закодирована
    filename - имя файла без расширения
    """

    os.makedirs(folder, exist_ok=True)

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )

    qr.add_data(text)
    qr.make(fit=True)

    image = qr.make_image(fill_color="black", back_color="white")

    path = os.path.join(folder, f"{filename}.png")
    image.save(path)

    return path