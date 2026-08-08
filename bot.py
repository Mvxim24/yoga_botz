def details_keyboard(product_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Согласен и продолжить",
                    callback_data=f"marketing_yes:{product_key}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="➡️ Продолжить без рассылки",
                    callback_data=f"marketing_no:{product_key}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 В меню",
                    callback_data="menu",
                )
            ],
        ]
    )