import qrcode
from PIL import Image


def generate_qr(
    data,
    output_path,
    logo_path=None
):

    # Create QR Code
    qr = qrcode.QRCode(
        version=None,

        # High error correction
        # allows logo in the center
        error_correction=qrcode.constants.ERROR_CORRECT_H,

        box_size=12,
        border=4
    )


    qr.add_data(data)

    qr.make(
        fit=True
    )


    # Create QR image
    qr_image = qr.make_image(
        fill_color="black",
        back_color="white"
    ).convert("RGB")


    # ==========================================
    # ADD BOT PROFILE PICTURE
    # ==========================================

    if logo_path:

        logo = Image.open(
            logo_path
        ).convert("RGBA")


        # Logo size
        logo_size = (
            qr_image.width // 5
        )


        logo.thumbnail(
            (
                logo_size,
                logo_size
            ),
            Image.Resampling.LANCZOS
        )


        # White background behind logo
        padding = 15


        logo_box = Image.new(
            "RGB",
            (
                logo.width + padding * 2,
                logo.height + padding * 2
            ),
            "white"
        )


        # Put logo on white background
        logo_box.paste(
            logo,
            (
                padding,
                padding
            ),
            logo
        )


        # ==========================================
        # CENTER LOGO
        # ==========================================

        x = (
            qr_image.width
            - logo_box.width
        ) // 2


        y = (
            qr_image.height
            - logo_box.height
        ) // 2


        qr_image.paste(
            logo_box,
            (
                x,
                y
            )
        )


    # ==========================================
    # SAVE QR
    # ==========================================

    qr_image.save(
        output_path,
        "PNG"
    )


    return output_path