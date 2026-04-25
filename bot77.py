import logging
import asyncio
import hashlib
from datetime import datetime
from decimal import Decimal
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# Настройки
API_TOKEN = '8453988894:AAHl1rRQK3uR4YLKCIYHIvJHU4KmTe8ai_w'
ADMIN_ID = [6691790154, 6081656814]  # ID администратора
BOT_USERNAME = 'logolinov_tg_bot'  # Без @

# Инициализация
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
logging.basicConfig(level=logging.INFO)

# База данных
tickets = {}
user_tickets = {}
current_ticket_id = 0

# База данных для балансов
user_balances = {}  # user_id -> {'balance': Decimal(0), 'transactions': []}
transaction_id_counter = 0

# Реферальная система
referral_links = {}  # user_id -> referral_code
referral_stats = {}  # user_id -> {'referrals': [], 'count': 0, 'earned': 0}
user_referrer = {}  # user_id -> referrer_id (кто пригласил)

# База данных для каталога и заказов
catalog = {}  # Товары с количеством
user_orders = {}  # user_id -> список заказов
order_id_counter = 0

# Настройки бота
bot_settings = {
    'maintenance': False,
    'maintenance_message': 'Бот на техническом обслуживании',
    'welcome_bonus': Decimal('100.00'),  # Бонус за регистрацию
    'referral_bonus': Decimal('50.00'),  # Бонус за реферала
}


# Состояния для FSM
class TicketStates(StatesGroup):
    waiting_for_ticket_message = State()
    waiting_for_admin_response = State()
    waiting_for_user_reply = State()


class BroadcastStates(StatesGroup):
    waiting_for_broadcast_message = State()


class AdminBalanceStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()
    waiting_for_description = State()


class CatalogStates(StatesGroup):
    waiting_for_product_name = State()
    waiting_for_product_price = State()
    waiting_for_product_quantity = State()
    waiting_for_product_description = State()


class AdminCatalogStates(StatesGroup):
    waiting_for_product_name = State()
    waiting_for_product_price = State()
    waiting_for_product_quantity = State()
    waiting_for_product_description = State()
    waiting_for_referral_settings = State()
    waiting_for_product_username = State()


class AdminSettingsStates(StatesGroup):
    waiting_for_welcome_bonus = State()
    waiting_for_referral_bonus = State()
    waiting_for_maintenance_message = State()


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def format_currency(amount):
    """Форматирование суммы с 2 знаками после запятой"""
    if isinstance(amount, Decimal):
        return f"{amount:.2f}"
    try:
        return f"{Decimal(str(amount)):.2f}"
    except:
        return str(amount)


def get_main_menu():
    """Основное меню для пользователей"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="🛒 Каталог")
    builder.button(text="📦 Мои заказы")
    builder.button(text="💰 Баланс")
    builder.button(text="🛟 Поддержка")
    builder.button(text="👥 Рефералы")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_admin_main_menu():
    """Главное меню для администраторов"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="🛒 Каталог")
    builder.button(text="📦 Мои заказы")
    builder.button(text="💰 Баланс")
    builder.button(text="🛟 Поддержка")
    builder.button(text="👥 Рефералы")
    builder.button(text="👨‍💼 Админ-панель")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_admin_panel_menu():
    """Меню административной панели"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="📊 Статистика")
    builder.button(text="🎫 Поддержка (Админ)")
    builder.button(text="💰 Управление балансами")
    builder.button(text="👥 Рефералы (Админ)")
    builder.button(text="📢 Рассылка")
    builder.button(text="⚙️ Настройки")
    builder.button(text="🛒 Управление каталогом")
    builder.button(text="🏠 Главное меню")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def is_admin(user_id):
    return user_id in ADMIN_ID


def generate_referral_link(user_id, custom_code=None):
    if custom_code:
        for uid, code in referral_links.items():
            if code == custom_code:
                return None
        referral_links[user_id] = custom_code
        return custom_code

    code = hashlib.md5(f"{user_id}{datetime.now().timestamp()}".encode()).hexdigest()[:8]
    referral_links[user_id] = code
    return code


def get_referral_stats(user_id):
    if user_id not in referral_stats:
        referral_stats[user_id] = {
            'referrals': [],
            'count': 0,
            'earned': Decimal('0.00'),
            'created_at': datetime.now()
        }
    return referral_stats[user_id]


async def process_new_user(user_id, referrer_code=None):
    if user_id not in user_balances:
        user_balances[user_id] = {
            'balance': bot_settings['welcome_bonus'],
            'transactions': [],
            'created_at': datetime.now()
        }
        global transaction_id_counter
        transaction_id_counter += 1
        user_balances[user_id]['transactions'].append({
            'id': transaction_id_counter,
            'type': 'bonus',
            'amount': bot_settings['welcome_bonus'],
            'status': 'completed',
            'created_at': datetime.now(),
            'description': 'Бонус за регистрацию'
        })

    if user_id not in referral_stats:
        referral_stats[user_id] = {
            'referrals': [],
            'count': 0,
            'earned': Decimal('0.00'),
            'created_at': datetime.now()
        }

    if referrer_code:
        for referrer_id, code in referral_links.items():
            if code == referrer_code:
                user_referrer[user_id] = referrer_id

                if referrer_id not in referral_stats:
                    referral_stats[referrer_id] = {
                        'referrals': [],
                        'count': 0,
                        'earned': Decimal('0.00'),
                        'created_at': datetime.now()
                    }

                if user_id not in referral_stats[referrer_id]['referrals']:
                    referral_stats[referrer_id]['referrals'].append(user_id)
                    referral_stats[referrer_id]['count'] += 1
                    referral_stats[referrer_id]['earned'] += bot_settings['referral_bonus']

                    if referrer_id in user_balances:
                        user_balances[referrer_id]['balance'] += bot_settings['referral_bonus']
                        transaction_id_counter += 1
                        user_balances[referrer_id]['transactions'].append({
                            'id': transaction_id_counter,
                            'type': 'referral',
                            'amount': bot_settings['referral_bonus'],
                            'status': 'completed',
                            'created_at': datetime.now(),
                            'description': f'Бонус за реферала (ID: {user_id})'
                        })

                try:
                    await bot.send_message(
                        referrer_id,
                        f"🎉 *Новый реферал!*\n\n"
                        f"Кто-то присоединился по вашей ссылке!\n"
                        f"Ваши рефералы: {referral_stats[referrer_id]['count']}\n"
                        f"+{format_currency(bot_settings['referral_bonus'])} ₽ на ваш счет!",
                        parse_mode="Markdown"
                    )
                except:
                    pass
                break


# ========== 🛒 КАТАЛОГ ==========

@dp.message(F.text == "🛒 Каталог")
async def show_catalog(message: Message):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    if not catalog:
        builder = ReplyKeyboardBuilder()
        builder.button(text="🛟 Поддержка")
        builder.button(text="🏠 Главное меню")
        builder.adjust(2)

        await message.answer(
            "🛒 *Каталог пуст*\n\n"
            "В настоящий момент товары отсутствуют.\n"
            "Обратитесь в поддержку для уточнения информации.",
            parse_mode="Markdown",
            reply_markup=builder.as_markup(resize_keyboard=True)
        )
        return

    builder = InlineKeyboardBuilder()
    for product_id, product in catalog.items():
        available = product.get('quantity', 0) > 0
        status_icon = "✅" if available else "⛔"
        builder.button(
            text=f"{status_icon} {product['name']} - {format_currency(product['price'])} ₽",
            callback_data=f"view_product_{product_id}"
        )
    builder.adjust(1)

    await message.answer(
        "🛒 *Каталог товаров и услуг*\n\n"
        "✅ - в наличии\n"
        "⛔ - нет в наличии\n\n"
        "Выберите товар для просмотра:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data.startswith('view_product_'))
async def view_product(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    product_id = int(callback_query.data.split('_')[2])
    product = catalog.get(product_id)

    if not product:
        await callback_query.answer("❌ Товар не найден!")
        return

    available = product.get('quantity', 0) > 0
    quantity_info = f"📊 *Количество:* {product.get('quantity', 0)} шт." if 'quantity' in product else ""
    status_emoji = "✅" if available else "⛔"
    status_text = "В наличии" if available else "Нет в наличии"

    # Информация о реферальной программе для товара
    referral_info = ""
    if product.get('referral_enabled', False):
        referral_bonus = product.get('referral_bonus', bot_settings['referral_bonus'])
        referral_info = f"\n👥 *Реферальная программа:* ✅ Включена\n💰 Бонус за реферала: {format_currency(referral_bonus)} ₽"
    else:
        referral_info = f"\n👥 *Реферальная программа:* ❌ Выключена"

    builder = InlineKeyboardBuilder()
    if available:
        builder.button(text="🛒 Купить", callback_data=f"buy_product_{product_id}")
    builder.button(text="⬅️ Назад", callback_data="back_to_catalog_main")
    builder.adjust(1)

    await callback_query.message.edit_text(
        f"📦 *{product['name']}*\n\n"
        f"💰 Цена: *{format_currency(product['price'])}* ₽\n"
        f"{quantity_info}"
        f"{referral_info}\n\n"
        f"📊 *Статус:* {status_emoji} {status_text}\n\n"
        f"📝 *Описание:*\n{product['description']}\n\n"
        f"📞 *Заказать:*\n"
        f"1. Нажмите '🛒 Купить'\n"
        f"2. Выберите количество\n"
        f"3. Оплатите заказ",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@dp.callback_query(F.data.startswith('buy_product_'))
async def buy_product(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    product_id = int(callback_query.data.split('_')[2])
    product = catalog.get(product_id)

    if not product:
        await callback_query.answer("❌ Товар не найден!")
        return

    available_quantity = product.get('quantity', 0)
    if available_quantity <= 0:
        await callback_query.answer("❌ Товар закончился!", show_alert=True)
        return

    if user_id not in user_balances:
        user_balances[user_id] = {'balance': Decimal('0.00'), 'transactions': []}

    # Цены с учетом скидок
    quantity_options = {
        1: Decimal(str(product['price'])),
        3: Decimal(str(product['price'])) * Decimal('2.7'),
        5: Decimal(str(product['price'])) * Decimal('4.5'),
        10: Decimal(str(product['price'])) * Decimal('9')
    }

    limited_options = {}
    for qty, total in quantity_options.items():
        if qty <= available_quantity:
            limited_options[qty] = total.quantize(Decimal('0.01'))

    if not limited_options:
        await callback_query.answer("❌ Недостаточно товара на складе!", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for qty, total in limited_options.items():
        builder.button(text=f"{qty} шт. - {format_currency(total)} ₽", callback_data=f"buy_qty_{product_id}_{qty}")

    builder.button(text="⬅️ Назад", callback_data=f"view_product_{product_id}")
    builder.adjust(1)

    discount_info = "\n".join(
        [f"• {qty} шт: {format_currency(total)} ₽ (экономия 10%)" for qty, total in limited_options.items() if
         qty in [3, 5, 10]])
    if not discount_info:
        discount_info = "• Скидки доступны при покупке 3, 5 или 10 штук"

    # Информация о реферальной программе
    referral_bonus_info = ""
    if product.get('referral_enabled', False):
        referral_bonus = product.get('referral_bonus', bot_settings['referral_bonus'])
        referral_bonus_info = f"\n👥 *Реферальная программа:* ✅ Включена\n💰 Бонус за реферала: {format_currency(referral_bonus)} ₽"

    await callback_query.message.edit_text(
        f"🛒 *Покупка: {product['name']}*\n\n"
        f"💰 Цена за 1 шт: {format_currency(product['price'])} ₽\n"
        f"📊 В наличии: {available_quantity} шт.\n"
        f"{referral_bonus_info}\n\n"
        f"🎁 *Скидки за количество:*\n{discount_info}\n\n"
        f"💳 Ваш баланс: {format_currency(user_balances[user_id]['balance'])} ₽\n\n"
        f"Выберите количество:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@dp.callback_query(F.data.startswith('buy_qty_'))
async def process_purchase_qty(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    data = callback_query.data.split('_')
    product_id = int(data[2])
    quantity = int(data[3])

    product = catalog.get(product_id)
    if not product:
        await callback_query.answer("❌ Товар не найден!")
        return

    available_quantity = product.get('quantity', 0)
    if available_quantity < quantity:
        await callback_query.answer(f"❌ Недостаточно товара! В наличии: {available_quantity} шт.", show_alert=True)
        return

    if user_id not in user_balances:
        user_balances[user_id] = {'balance': Decimal('0.00'), 'transactions': []}

    # Расчет суммы с учетом скидок
    price = Decimal(str(product['price']))
    if quantity == 1:
        total_price = price
    elif quantity == 3:
        total_price = price * Decimal('2.7')
    elif quantity == 5:
        total_price = price * Decimal('4.5')
    elif quantity == 10:
        total_price = price * Decimal('9')
    else:
        total_price = price * quantity

    total_price = total_price.quantize(Decimal('0.01'))

    if user_balances[user_id]['balance'] < total_price:
        builder = InlineKeyboardBuilder()
        builder.button(text="💰 Пополнить баланс", callback_data="deposit_custom")
        builder.button(text="⬅️ Назад", callback_data=f"buy_product_{product_id}")
        builder.adjust(1)

        await callback_query.message.edit_text(
            f"❌ *Недостаточно средств!*\n\n"
            f"💳 Необходимо: {format_currency(total_price)} ₽\n"
            f"💰 Ваш баланс: {format_currency(user_balances[user_id]['balance'])} ₽\n\n"
            f"Пополните баланс для покупки:",
            parse_mode="Markdown",
            reply_markup=builder.as_markup()
        )
        await callback_query.answer()
        return

    global order_id_counter
    order_id_counter += 1

    order = {
        'id': order_id_counter,
        'product_id': product_id,
        'product_name': product['name'],
        'quantity': quantity,
        'price_per_item': price,
        'total_price': total_price,
        'status': 'processing',
        'created_at': datetime.now(),
        'user_name': callback_query.from_user.full_name,
        'username': callback_query.from_user.username,
        'user_id': user_id
    }

    if user_id not in user_orders:
        user_orders[user_id] = []
    user_orders[user_id].append(order)

    # Списание средств
    user_balances[user_id]['balance'] -= total_price

    # Обновление количества товара
    catalog[product_id]['quantity'] -= quantity
    if catalog[product_id]['quantity'] <= 0:
        catalog[product_id]['quantity'] = 0

    global transaction_id_counter
    transaction_id_counter += 1
    user_balances[user_id]['transactions'].append({
        'id': transaction_id_counter,
        'type': 'purchase',
        'amount': -total_price,
        'status': 'completed',
        'created_at': datetime.now(),
        'description': f'Покупка {product["name"]} x{quantity} (Заказ #{order_id_counter})'
    })

    # Проверка реферальной программы для товара
    if product.get('referral_enabled', False) and user_id in user_referrer:
        referrer_id = user_referrer[user_id]
        if referrer_id:
            referral_bonus = product.get('referral_bonus', bot_settings['referral_bonus'])

            if referrer_id not in referral_stats:
                referral_stats[referrer_id] = {
                    'referrals': [],
                    'count': 0,
                    'earned': Decimal('0.00'),
                    'created_at': datetime.now()
                }

            if user_id not in referral_stats[referrer_id]['referrals']:
                referral_stats[referrer_id]['referrals'].append(user_id)
                referral_stats[referrer_id]['count'] += 1
                referral_stats[referrer_id]['earned'] += referral_bonus

                if referrer_id in user_balances:
                    user_balances[referrer_id]['balance'] += referral_bonus
                    transaction_id_counter += 1
                    user_balances[referrer_id]['transactions'].append({
                        'id': transaction_id_counter,
                        'type': 'referral_product',
                        'amount': referral_bonus,
                        'status': 'completed',
                        'created_at': datetime.now(),
                        'description': f'Реферальный бонус за покупку товара "{product["name"]}" (ID реферала: {user_id}, Заказ #{order_id_counter})'
                    })

                    try:
                        await bot.send_message(
                            referrer_id,
                            f"🎉 *Реферальный бонус за покупку товара!*\n\n"
                            f"Ваш реферал купил товар: *{product['name']}*\n"
                            f"🛒 Количество: {quantity} шт.\n"
                            f"💰 Сумма заказа: {format_currency(total_price)} ₽\n"
                            f"🎁 Ваш бонус: +{format_currency(referral_bonus)} ₽\n"
                            f"💳 Новый баланс: {format_currency(user_balances[referrer_id]['balance'])} ₽",
                            parse_mode="Markdown"
                        )
                    except:
                        pass

    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Мои заказы", callback_data="view_orders_user")
    builder.button(text="💬 Поддержка", callback_data="create_ticket_quick")
    builder.adjust(1)

    await callback_query.message.edit_text(
        f"✅ *Заказ создан!*\n\n"
        f"🆔 Номер заказа: *#{order_id_counter}*\n"
        f"📦 Товар: {product['name']}\n"
        f"📊 Количество: {quantity} шт.\n"
        f"💰 Сумма: {format_currency(total_price)} ₽\n"
        f"💳 Списано с баланса: {format_currency(total_price)} ₽\n"
        f"💰 Остаток на балансе: {format_currency(user_balances[user_id]['balance'])} ₽\n"
        f"📊 Остаток товара: {catalog[product_id]['quantity']} шт.\n\n"
        f"📞 *Статус:* В обработке\n"
        f"⏱️ *Время создания:* {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        f"📋 Вы можете отслеживать статус заказа в разделе 'Мои заказы'\n"
        f"❓ По вопросам обращайтесь в поддержку",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )

    # Уведомление админам
    for admin_id in ADMIN_ID:
        try:
            await bot.send_message(
                admin_id,
                f"🛒 *НОВЫЙ ЗАКАЗ!* #{order_id_counter}\n\n"
                f"👤 *Пользователь:* {callback_query.from_user.full_name}\n"
                f"📧 @{callback_query.from_user.username if callback_query.from_user.username else 'нет'}\n"
                f"🆔 ID: `{user_id}`\n\n"
                f"📦 *Товар:* {product['name']}\n"
                f"📊 *Количество:* {quantity} шт.\n"
                f"💰 *Сумма:* {format_currency(total_price)} ₽\n"
                f"💳 *Списано с баланса:* {format_currency(total_price)} ₽\n"
                f"💰 *Баланс после списания:* {format_currency(user_balances[user_id]['balance'])} ₽\n"
                f"📊 *Остаток товара:* {catalog[product_id]['quantity']} шт.\n\n"
                f"⏱️ *Время:* {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                parse_mode="Markdown"
            )
        except:
            pass

    await callback_query.answer("✅ Заказ успешно создан!")


@dp.callback_query(F.data == "back_to_catalog_main")
async def back_to_catalog_main(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    if not catalog:
        await callback_query.message.edit_text(
            "🛒 *Каталог пуст*\n\n"
            "В настоящий момент товары отсутствуют.",
            parse_mode="Markdown"
        )
        return

    builder = InlineKeyboardBuilder()
    for product_id, product in catalog.items():
        available = product.get('quantity', 0) > 0
        status_icon = "✅" if available else "⛔"
        builder.button(
            text=f"{status_icon} {product['name']} - {format_currency(product['price'])} ₽",
            callback_data=f"view_product_{product_id}"
        )
    builder.adjust(1)

    await callback_query.message.edit_text(
        "🛒 *Каталог товаров и услуг*\n\n"
        "✅ - в наличии\n"
        "⛔ - нет в наличии\n\n"
        "Выберите товар для просмотра:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


# ========== 📦 МОИ ЗАКАЗЫ ==========

@dp.message(F.text == "📦 Мои заказы")
async def show_orders(message: Message):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    if user_id not in user_orders or not user_orders[user_id]:
        builder = InlineKeyboardBuilder()
        builder.button(text="🛒 Перейти в каталог", callback_data="back_to_catalog_main")
        builder.adjust(1)

        await message.answer(
            "📭 *У вас пока нет заказов*\n\n"
            "Перейдите в каталог, чтобы сделать первый заказ!",
            parse_mode="Markdown",
            reply_markup=builder.as_markup()
        )
        return

    orders = user_orders[user_id]

    total_orders = len(orders)
    total_spent = sum(order['total_price'] for order in orders)
    active_orders = sum(1 for order in orders if order['status'] in ['processing', 'pending'])

    orders_text = f"📦 *Мои заказы*\n\n"
    orders_text += f"📊 *Статистика:*\n"
    orders_text += f"• Всего заказов: {total_orders}\n"
    orders_text += f"• Активных: {active_orders}\n"
    orders_text += f"• Потрачено: {format_currency(total_spent)} ₽\n\n"
    orders_text += f"📋 *Последние заказы:*\n\n"

    for order in sorted(orders, key=lambda x: x['created_at'], reverse=True)[:5]:
        status_icon = "⏳" if order['status'] == 'processing' else "✅" if order['status'] == 'completed' else "❌"
        status_text = "В обработке" if order['status'] == 'processing' else "Выполнен" if order[
                                                                                              'status'] == 'completed' else "Отменен"

        orders_text += f"{status_icon} *Заказ #{order['id']}*\n"
        orders_text += f"📦 {order['product_name']} x{order['quantity']}\n"
        orders_text += f"💰 {format_currency(order['total_price'])} ₽\n"
        orders_text += f"📅 {order['created_at'].strftime('%d.%m.%Y %H:%M')}\n"
        orders_text += f"📊 Статус: {status_text}\n\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Все заказы", callback_data="view_orders_user")
    builder.button(text="🔄 Обновить", callback_data="refresh_orders")
    builder.button(text="🛒 Новый заказ", callback_data="back_to_catalog_main")
    builder.adjust(2, 1)

    await message.answer(orders_text, parse_mode="Markdown")
    await message.answer(
        "Действия с заказами:",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data == "view_orders_user")
async def view_all_orders_user(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    if user_id not in user_orders or not user_orders[user_id]:
        await callback_query.message.edit_text(
            "📭 У вас нет заказов.",
            reply_markup=InlineKeyboardBuilder()
            .button(text="🛒 В каталог", callback_data="back_to_catalog_main")
            .as_markup()
        )
        return

    orders = user_orders[user_id]

    builder = InlineKeyboardBuilder()
    for order in orders:
        status_icon = "⏳" if order['status'] == 'processing' else "✅" if order['status'] == 'completed' else "❌"
        builder.button(
            text=f"{status_icon} Заказ #{order['id']}",
            callback_data=f"view_order_detail_{order['id']}"
        )

    builder.button(text="🛒 Новый заказ", callback_data="back_to_catalog_main")
    builder.button(text="⬅️ Назад", callback_data="back_to_orders_main")
    builder.adjust(1)

    await callback_query.message.edit_text(
        f"📦 *Все заказы*\n\n"
        f"📊 Найдено заказов: {len(orders)}\n\n"
        f"Выберите заказ для просмотра деталей:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@dp.callback_query(F.data == "back_to_orders_main")
async def back_to_orders_main(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    if user_id not in user_orders or not user_orders[user_id]:
        await callback_query.message.edit_text(
            "📭 У вас нет заказов.",
            reply_markup=InlineKeyboardBuilder()
            .button(text="🛒 В каталог", callback_data="back_to_catalog_main")
            .as_markup()
        )
        return

    orders = user_orders[user_id]

    orders_text = f"📦 *Мои заказы*\n\n"
    for order in sorted(orders, key=lambda x: x['created_at'], reverse=True)[:5]:
        status_icon = "⏳" if order['status'] == 'processing' else "✅" if order['status'] == 'completed' else "❌"
        status_text = "В обработке" if order['status'] == 'processing' else "Выполнен" if order[
                                                                                              'status'] == 'completed' else "Отменен"

        orders_text += f"{status_icon} *Заказ #{order['id']}*\n"
        orders_text += f"📦 {order['product_name']} x{order['quantity']}\n"
        orders_text += f"💰 {format_currency(order['total_price'])} ₽\n"
        orders_text += f"📅 {order['created_at'].strftime('%d.%m.%Y %H:%M')}\n"
        orders_text += f"📊 Статус: {status_text}\n\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Все заказы", callback_data="view_orders_user")
    builder.button(text="🛒 Новый заказ", callback_data="back_to_catalog_main")
    builder.adjust(2)

    await callback_query.message.edit_text(
        orders_text,
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@dp.callback_query(F.data == "refresh_orders")
async def refresh_orders(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    if user_id not in user_orders or not user_orders[user_id]:
        await callback_query.message.edit_text(
            "📭 У вас нет заказов.",
            reply_markup=InlineKeyboardBuilder()
            .button(text="🛒 В каталог", callback_data="back_to_catalog_main")
            .as_markup()
        )
        return

    orders = user_orders[user_id][-5:]

    orders_text = "📦 *Последние заказы*\n\n"
    for order in reversed(orders):
        status_icon = "⏳" if order['status'] == 'processing' else "✅" if order['status'] == 'completed' else "❌"
        status_text = "В обработке" if order['status'] == 'processing' else "Выполнен" if order[
                                                                                              'status'] == 'completed' else "Отменен"

        orders_text += f"{status_icon} *Заказ #{order['id']}*\n"
        orders_text += f"📦 {order['product_name']} x{order['quantity']}\n"
        orders_text += f"💰 {format_currency(order['total_price'])} ₽\n"
        orders_text += f"📅 {order['created_at'].strftime('%d.%m.%Y %H:%M')}\n"
        orders_text += f"📊 Статус: {status_text}\n\n"

    await callback_query.message.edit_text(
        orders_text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardBuilder()
        .button(text="📋 Все заказы", callback_data="view_orders_user")
        .button(text="🛒 Новый заказ", callback_data="back_to_catalog_main")
        .adjust(2)
        .as_markup()
    )
    await callback_query.answer("✅ Список обновлен!")


@dp.callback_query(F.data.startswith('view_order_detail_'))
async def view_order_detail(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    order_id = int(callback_query.data.split('_')[3])

    # Поиск заказа
    found_order = None
    for orders in user_orders.values():
        for order in orders:
            if order['id'] == order_id:
                found_order = order
                break
        if found_order:
            break

    if not found_order:
        await callback_query.answer("❌ Заказ не найден!")
        return

    status_icon = "⏳" if found_order['status'] == 'processing' else "✅" if found_order['status'] == 'completed' else "❌"
    status_text = "В обработке" if found_order['status'] == 'processing' else "Выполнен" if found_order[
                                                                                                'status'] == 'completed' else "Отменен"

    order_text = (
        f"📦 *Заказ #{found_order['id']}*\n\n"
        f"🛒 Товар: {found_order['product_name']}\n"
        f"📊 Количество: {found_order['quantity']} шт.\n"
        f"💰 Цена за шт.: {format_currency(found_order['price_per_item'])} ₽\n"
        f"💳 Итого: {format_currency(found_order['total_price'])} ₽\n"
        f"📊 Статус: {status_icon} {status_text}\n"
        f"📅 Дата: {found_order['created_at'].strftime('%d.%m.%Y %H:%M')}\n\n"
        f"👤 Пользователь: {found_order['user_name']}\n"
        f"📧 @{found_order['username'] if found_order['username'] else 'нет'}"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад", callback_data="view_orders_user")

    await callback_query.message.edit_text(
        order_text,
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


# ========== 💰 БАЛАНС ==========

@dp.message(F.text == "💰 Баланс")
async def balance_menu(message: Message):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    if user_id not in user_balances:
        user_balances[user_id] = {
            'balance': bot_settings['welcome_bonus'] if user_id in referral_stats else Decimal('0.00'),
            'transactions': [],
            'created_at': datetime.now()
        }
        if bot_settings['welcome_bonus'] > 0 and user_id in referral_stats:
            global transaction_id_counter
            transaction_id_counter += 1
            user_balances[user_id]['transactions'].append({
                'id': transaction_id_counter,
                'type': 'bonus',
                'amount': bot_settings['welcome_bonus'],
                'status': 'completed',
                'created_at': datetime.now(),
                'description': 'Бонус за регистрацию'
            })

    balance = user_balances[user_id]['balance']
    ref_earned = referral_stats.get(user_id, {}).get('earned', Decimal('0.00'))

    total_deposits = sum(t['amount'] for t in user_balances[user_id]['transactions']
                         if t['type'] == 'deposit' and t['status'] == 'completed')
    total_purchases = sum(abs(t['amount']) for t in user_balances[user_id]['transactions']
                          if t['type'] == 'purchase')
    total_referrals = sum(t['amount'] for t in user_balances[user_id]['transactions']
                          if t['type'] in ['referral', 'referral_product'])

    builder = ReplyKeyboardBuilder()
    builder.button(text="💰 Пополнить баланс")
    builder.button(text="👥 Пригласить друга")
    builder.button(text="🏠 Главное меню")
    builder.adjust(2, 1)

    await message.answer(
        f"💰 *Ваш баланс*\n\n"
        f"💳 *Текущий баланс:* *{format_currency(balance)}* ₽\n\n"
        f"📊 *Статистика:*\n"
        f"• Пополнено: {format_currency(total_deposits)} ₽\n"
        f"• Потрачено: {format_currency(total_purchases)} ₽\n"
        f"• Реферальные бонусы: {format_currency(ref_earned)} ₽\n"
        f"• Получено реферальных бонусов: {format_currency(total_referrals)} ₽\n\n"
        f"📈 *Доступные действия:*",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )


# ========== РЕФЕРАЛЬНАЯ ПРОГРАММА ДЛЯ ОБЫЧНЫХ ПОЛЬЗОВАТЕЛЕЙ ==========

@dp.message(F.text == "👥 Рефералы")
async def show_referral_user_menu(message: Message):
    """Меню реферальной программы для обычных пользователей"""
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    builder = ReplyKeyboardBuilder()
    builder.button(text="🔗 Моя реферальная ссылка")
    builder.button(text="📊 Моя статистика")
    builder.button(text="💰 Реферальные товары")
    builder.button(text="🏠 Главное меню")
    if is_admin_user:
        builder.button(text="👨‍💼 Админ-панель")
    builder.adjust(2, 1, 2)

    await message.answer(
        "👥 *Реферальная программа*\n\n"
        "Приглашайте друзей и получайте бонусы!\n\n"
        "✨ *Что вы получаете:*\n"
        f"• За каждого друга: +{format_currency(bot_settings['referral_bonus'])} ₽\n"
        f"• Друг получает: +{format_currency(bot_settings['welcome_bonus'])} ₽\n"
        "• За покупки друзей: дополнительные бонусы\n"
        "• Специальные призы за активность\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )


@dp.message(F.text == "🔗 Моя реферальная ссылка")
async def show_my_referral_link(message: Message):
    """Показывает реферальную ссылку пользователя"""
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    if user_id not in referral_links:
        code = generate_referral_link(user_id)
    else:
        code = referral_links[user_id]

    referral_url = f"https://t.me/{BOT_USERNAME}?start={code}"
    stats = get_referral_stats(user_id)

    # Получаем товары с реферальной программой
    referral_products = []
    for product_id, product in catalog.items():
        if product.get('referral_enabled', False):
            referral_bonus = product.get('referral_bonus', bot_settings['referral_bonus'])
            referral_products.append(f"• {product['name']}: +{format_currency(referral_bonus)} ₽ за покупку")

    referral_products_text = ""
    if referral_products:
        referral_products_text = "\n🎁 *Товары с бонусами:*\n" + "\n".join(referral_products[:5])

    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Поделиться ссылкой", callback_data="share_referral")
    builder.button(text="📊 Полная статистика", callback_data="user_ref_stats_full")
    builder.adjust(1)

    await message.answer(
        f"🔗 *Ваша реферальная ссылка:*\n\n"
        f"`{referral_url}`\n\n"
        f"💎 *Ваш код:* `{code}`\n\n"
        f"📊 *Ваша статистика:*\n"
        f"• Приглашено друзей: {stats['count']}\n"
        f"• Заработано бонусов: {format_currency(stats['earned'])} ₽\n\n"
        f"💰 *Как зарабатывать:*\n"
        f"1. Друг переходит по вашей ссылке\n"
        f"2. Получает +{format_currency(bot_settings['welcome_bonus'])} ₽ бонус\n"
        f"3. Вы получаете +{format_currency(bot_settings['referral_bonus'])} ₽\n"
        f"4. Когда друг покупает товары - вы получаете ещё бонусы!\n"
        f"{referral_products_text}\n\n"
        f"🏆 *Дополнительные награды:*\n"
        f"• 5 друзей: +100 ₽\n"
        f"• 10 друзей: +250 ₽\n"
        f"• 20 друзей: +500 ₽",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )


@dp.message(F.text == "📊 Моя статистика")
async def show_my_referral_stats(message: Message):
    """Показывает детальную статистику рефералов"""
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    stats = get_referral_stats(user_id)

    # Получаем транзакции реферальных бонусов
    referral_transactions = []
    if user_id in user_balances:
        for transaction in user_balances[user_id]['transactions']:
            if transaction['type'] in ['referral', 'referral_product']:
                referral_transactions.append(transaction)

    total_referral_income = sum(t['amount'] for t in referral_transactions)

    if stats['referrals']:
        refs_text = "\n📋 *Ваши рефералы:*\n"
        for i, ref_id in enumerate(stats['referrals'][:10], 1):
            refs_text += f"{i}. ID: `{ref_id}`\n"
        if len(stats['referrals']) > 10:
            refs_text += f"... и еще {len(stats['referrals']) - 10}\n"
    else:
        refs_text = "\n📭 У вас пока нет рефералов"

    referral_transactions_text = ""
    if referral_transactions:
        referral_transactions_text = "\n💰 *История бонусов:*\n"
        for i, trans in enumerate(sorted(referral_transactions, key=lambda x: x['created_at'], reverse=True)[:5], 1):
            date_str = trans['created_at'].strftime('%d.%m.%Y %H:%M')
            amount = f"+{format_currency(trans['amount'])} ₽"
            referral_transactions_text += f"{i}. {amount} - {trans['description']} ({date_str})\n"

    builder = ReplyKeyboardBuilder()
    builder.button(text="🔗 Моя реферальная ссылка")
    builder.button(text="💰 Реферальные товары")
    builder.button(text="🏠 Главное меню")
    if is_admin_user:
        builder.button(text="👨‍💼 Админ-панель")
    builder.adjust(2)

    await message.answer(
        f"📊 *Ваша реферальная статистика*\n\n"
        f"👥 Всего рефералов: {stats['count']}\n"
        f"💰 Заработано баллов: {format_currency(stats['earned'])} ₽\n"
        f"💸 Получено бонусов: {format_currency(total_referral_income)} ₽\n"
        f"📅 С вами с: {stats['created_at'].strftime('%d.%m.%Y')}\n"
        f"{refs_text}"
        f"{referral_transactions_text}",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )


@dp.message(F.text == "💰 Реферальные товары")
async def show_referral_products(message: Message):
    """Показывает товары с реферальной программой"""
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    # Фильтруем товары с реферальной программой
    referral_products = []
    for product_id, product in catalog.items():
        if product.get('referral_enabled', False):
            referral_bonus = product.get('referral_bonus', bot_settings['referral_bonus'])
            referral_products.append((product_id, product, referral_bonus))

    if not referral_products:
        builder = ReplyKeyboardBuilder()
        builder.button(text="🔗 Моя реферальная ссылка")
        builder.button(text="📊 Моя статистика")
        builder.button(text="🏠 Главное меню")
        if is_admin_user:
            builder.button(text="👨‍💼 Админ-панель")
        builder.adjust(2)

        await message.answer(
            "🛒 *Товары с реферальной программой*\n\n"
            "Пока нет товаров с реферальной программой.\n"
            "Следите за обновлениями!",
            parse_mode="Markdown",
            reply_markup=builder.as_markup(resize_keyboard=True)
        )
        return

    products_text = "🎁 *Товары с реферальной программой*\n\n"
    products_text += "За покупку этих товаров вашими рефералами вы получаете бонусы!\n\n"

    for product_id, product, referral_bonus in referral_products[:10]:
        available = product.get('quantity', 0) > 0
        status_icon = "✅" if available else "⛔"
        products_text += f"{status_icon} *{product['name']}*\n"
        products_text += f"💰 Цена: {format_currency(product['price'])} ₽\n"
        products_text += f"🎁 Ваш бонус: +{format_currency(referral_bonus)} ₽\n"
        products_text += f"📝 {product['description'][:100]}...\n\n"

    if len(referral_products) > 10:
        products_text += f"📦 И еще {len(referral_products) - 10} товаров...\n\n"

    products_text += "💡 *Как это работает:*\n"
    products_text += "1. Ваш друг переходит по вашей ссылке\n"
    products_text += "2. Покупает любой товар из списка выше\n"
    products_text += f"3. Вы автоматически получаете бонус!"

    builder = InlineKeyboardBuilder()
    builder.button(text="🛒 Перейти в каталог", callback_data="back_to_catalog_main")
    builder.button(text="🔗 Моя реферальная ссылка", callback_data="share_referral")
    builder.adjust(1)

    await message.answer(
        products_text,
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data == "user_ref_stats_full")
async def user_ref_stats_full(callback_query: CallbackQuery):
    """Полная статистика рефералов (через inline кнопку)"""
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    stats = get_referral_stats(user_id)

    # Получаем транзакции реферальных бонусов
    referral_transactions = []
    if user_id in user_balances:
        for transaction in user_balances[user_id]['transactions']:
            if transaction['type'] in ['referral', 'referral_product']:
                referral_transactions.append(transaction)

    total_referral_income = sum(t['amount'] for t in referral_transactions)

    if stats['referrals']:
        refs_text = "\n📋 *Полный список рефералов:*\n"
        for i, ref_id in enumerate(stats['referrals'], 1):
            refs_text += f"{i}. ID: `{ref_id}`\n"
    else:
        refs_text = "\n📭 У вас пока нет рефералов"

    referral_transactions_text = ""
    if referral_transactions:
        referral_transactions_text = "\n💰 *Вся история бонусов:*\n"
        for i, trans in enumerate(sorted(referral_transactions, key=lambda x: x['created_at'], reverse=True), 1):
            date_str = trans['created_at'].strftime('%d.%m.%Y %H:%M')
            amount = f"+{format_currency(trans['amount'])} ₽"
            referral_transactions_text += f"{i}. {amount} - {trans['description']} ({date_str})\n"

    await callback_query.message.answer(
        f"📊 *Полная реферальная статистика*\n\n"
        f"👥 Всего рефералов: {stats['count']}\n"
        f"💰 Заработано баллов: {format_currency(stats['earned'])} ₽\n"
        f"💸 Получено бонусов: {format_currency(total_referral_income)} ₽\n"
        f"📅 С вами с: {stats['created_at'].strftime('%d.%m.%Y')}\n"
        f"{refs_text}"
        f"{referral_transactions_text}",
        parse_mode="Markdown"
    )
    await callback_query.answer()


@dp.message(F.text == "💰 Пополнить баланс")
async def deposit_balance(message: Message):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="100 ₽", callback_data="deposit_100")
    builder.button(text="500 ₽", callback_data="deposit_500")
    builder.button(text="1000 ₽", callback_data="deposit_1000")
    builder.button(text="2000 ₽", callback_data="deposit_2000")
    builder.button(text="5000 ₽", callback_data="deposit_5000")
    builder.button(text="Другая сумма", callback_data="deposit_custom")
    builder.adjust(2)

    await message.answer(
        "💰 *Пополнение баланса*\n\n"
        "Выберите сумму для пополнения:\n\n"
        "💡 *Минимальная сумма:* 100 ₽\n"
        "💡 *Максимальная сумма:* 50,000 ₽",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data.startswith('deposit_'))
async def process_deposit(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    if callback_query.data == "deposit_custom":
        await callback_query.message.answer(
            "💰 *Введите сумму для пополнения:*\n\n"
            "Пример: 1500 или 1500.50 (для 1500 рублей 50 копеек)\n"
            "Минимум: 100 ₽\n"
            "Максимум: 50,000 ₽",
            parse_mode="Markdown"
        )
        await callback_query.answer()
        return

    amount_str = callback_query.data.split('_')[1]

    if amount_str.isdigit():
        amount = Decimal(amount_str)

        if user_id not in user_balances:
            user_balances[user_id] = {
                'balance': Decimal('0.00'),
                'transactions': [],
                'created_at': datetime.now()
            }

        global transaction_id_counter
        transaction_id_counter += 1

        transaction = {
            'id': transaction_id_counter,
            'type': 'deposit',
            'amount': amount,
            'status': 'pending',
            'created_at': datetime.now(),
            'description': f'Пополнение на {format_currency(amount)} ₽'
        }

        user_balances[user_id]['transactions'].append(transaction)

        # Генерация ссылки на оплату (замените на реальную платежную систему)
        payment_url = f"https://payment.example.com/pay/{user_id}/{amount}"

        builder = InlineKeyboardBuilder()
        builder.button(text="💳 Перейти к оплате", url=payment_url)
        builder.button(text="✅ Я оплатил", callback_data=f"confirm_payment_{transaction_id_counter}")
        builder.adjust(1)

        await callback_query.message.answer(
            f"💳 *Оплата {format_currency(amount)} ₽*\n\n"
            f"📋 *Детали платежа:*\n"
            f"• Сумма: {format_currency(amount)} ₽\n"
            f"• ID транзакции: #{transaction_id_counter}\n"
            f"• Статус: Ожидает оплаты\n\n"
            f"🔗 *Ссылка для оплата:*\n"
            f"{payment_url}\n\n"
            f"💰 После оплаты нажмите '✅ Я оплатил'",
            parse_mode="Markdown",
            reply_markup=builder.as_markup()
        )

    await callback_query.answer()


@dp.callback_query(F.data.startswith('confirm_payment_'))
async def confirm_payment(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    transaction_id = int(callback_query.data.split('_')[2])

    if user_id in user_balances:
        for transaction in user_balances[user_id]['transactions']:
            if transaction['id'] == transaction_id and transaction['status'] == 'pending':
                transaction['status'] = 'completed'
                user_balances[user_id]['balance'] += transaction['amount']

                await callback_query.message.answer(
                    f"✅ *Оплата подтверждена!*\n\n"
                    f"💰 На ваш баланс зачислено: *{format_currency(transaction['amount'])}* ₽\n"
                    f"💳 Новый баланс: *{format_currency(user_balances[user_id]['balance'])}* ₽\n"
                    f"📋 ID транзакции: #{transaction_id}",
                    parse_mode="Markdown"
                )

                # Уведомление админам
                for admin_id in ADMIN_ID:
                    try:
                        await bot.send_message(
                            admin_id,
                            f"💰 *НОВОЕ ПОПОЛНЕНИЕ*\n\n"
                            f"👤 Пользователь: {callback_query.from_user.full_name}\n"
                            f"🆔 ID: `{user_id}`\n"
                            f"💳 Сумма: {format_currency(transaction['amount'])} ₽\n"
                            f"📋 ID транзакции: #{transaction_id}",
                            parse_mode="Markdown"
                        )
                    except:
                        pass

                await callback_query.answer("✅ Баланс пополнен!")
                return

    await callback_query.answer("❌ Транзакция не найдена или уже обработана")


@dp.callback_query(F.data == "share_referral")
async def share_referral_link(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    if user_id not in referral_links:
        code = generate_referral_link(user_id)
    else:
        code = referral_links[user_id]

    referral_url = f"https://t.me/{BOT_USERNAME}?start={code}"

    share_text = (
        f"🎁 *Привет! У меня для тебя подарок!*\n\n"
        f"Присоединяйся к нашему сервису по моей ссылке и получи *{format_currency(bot_settings['welcome_bonus'])}* ₽ на баланс:\n\n"
        f"{referral_url}\n\n"
        f"💎 Используй код: `{code}`\n\n"
        f"✨ *Что внутри:*\n"
        f"• Дизайн и разработка\n"
        f"• Копирайтинг и SMM\n"
        f"• Видеомонтаж и SEO\n"
        f"• И многое другое!\n\n"
        f"💫 *Бонусы для тебя:*\n"
        f"• Приветственный бонус {format_currency(bot_settings['welcome_bonus'])} ₽\n"
        f"• Реферальная программа\n"
        f"• Широкий выбор товаров и услуг\n\n"
        f"Жду тебя! 😊"
    )

    await callback_query.message.answer(
        f"📢 *Текст для отправки другу:*\n\n{share_text}\n\n"
        f"💡 *Совет:* Скопируйте этот текст и отправьте другу в личные сообщения!",
        parse_mode="Markdown"
    )
    await callback_query.answer("✅ Текст готов для отправки!")


@dp.callback_query(F.data == "user_ref_stats")
async def user_ref_stats(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    stats = get_referral_stats(user_id)

    # Получаем детальную информацию о реферальных транзакциях
    referral_transactions = []
    if user_id in user_balances:
        for transaction in user_balances[user_id]['transactions']:
            if transaction['type'] in ['referral', 'referral_product']:
                referral_transactions.append({
                    'amount': transaction['amount'],
                    'description': transaction['description'],
                    'date': transaction['created_at']
                })

    total_referral_income = sum(t['amount'] for t in referral_transactions)

    if stats['referrals']:
        refs_text = "\n📋 *Список рефералов:*\n"
        for i, ref_id in enumerate(stats['referrals'], 1):
            refs_text += f"{i}. ID: `{ref_id}`\n"
    else:
        refs_text = "\n📭 У вас пока нет рефералов"

    referral_transactions_text = ""
    if referral_transactions:
        referral_transactions_text = "\n💰 *История реферальных бонусов:*\n"
        for i, trans in enumerate(sorted(referral_transactions, key=lambda x: x['date'], reverse=True)[:10], 1):
            date_str = trans['date'].strftime('%d.%m.%Y %H:%M')
            referral_transactions_text += f"{i}. +{format_currency(trans['amount'])} ₽ - {trans['description']} ({date_str})\n"

    await callback_query.message.answer(
        f"📊 *Ваша статистика*\n\n"
        f"👥 Всего рефералов: {stats['count']}\n"
        f"💰 Заработано баллов: {format_currency(stats['earned'])}\n"
        f"💸 Получено реферальных бонусов: {format_currency(total_referral_income)} ₽\n"
        f"📅 Дата регистрации: {stats['created_at'].strftime('%d.%m.%Y')}\n"
        f"{refs_text}"
        f"{referral_transactions_text}",
        parse_mode="Markdown"
    )
    await callback_query.answer()


# ========== 🛟 ПОДДЕРЖКА ==========

@dp.message(F.text == "🛟 Поддержка")
async def support_menu(message: Message):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    builder = ReplyKeyboardBuilder()
    builder.button(text="🎫 Создать тикет")
    builder.button(text="📋 Мои тикеты")
    builder.button(text="🏠 Главное меню")
    if is_admin_user:
        builder.button(text="👨‍💼 Админ-панель")
    builder.adjust(2, 1)

    await message.answer(
        "🛟 *Служба поддержки*\n\n"
        "Мы всегда готовы помочь вам!\n\n"
        "✨ *Что мы можем решить:*\n"
        "• Проблемы с заказами\n"
        "• Вопросы по оплате\n"
        "• Технические проблемы\n"
        "• Предложения и жалобы\n\n"
        "⏱️ *Время ответа:*\n"
        "• В рабочее время: до 15 минут\n"
        "• В нерабочее время: до 24 часов\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )


# ========== 🛟 ПОДДЕРЖКА (Админ) ==========

@dp.message(F.text == "🎫 Поддержка (Админ)")
async def admin_support_menu(message: Message):
    """Админское меню поддержки - доступно только из админ-панели"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта функция доступна только администраторам.")
        return

    builder = ReplyKeyboardBuilder()
    builder.button(text="📋 Открытые тикеты")
    builder.button(text="📊 Статистика тикетов")
    builder.button(text="👨‍💼 Админ-панель")
    builder.adjust(1)

    await message.answer(
        "🎫 *Управление поддержкой (Админ)*\n\n"
        "Здесь вы можете управлять тикетами пользователей\n\n"
        "Выберите действия:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )


@dp.message(F.text == "🎫 Создать тикет")
async def create_ticket_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    await state.set_state(TicketStates.waiting_for_ticket_message)

    builder = ReplyKeyboardBuilder()
    builder.button(text="❌ Отмена")
    builder.adjust(1)

    await message.answer(
        "🎫 *Создание нового тикета*\n\n"
        "Опишите вашу проблему или вопрос:\n\n"
        "💡 *Советы:*\n"
        "• Будьте максимально подробны\n"
        "• Укажите номер заказа, если есть\n"
        "• Прикрепите скриншоты, если нужно\n\n"
        "Для отмены нажмите кнопку ниже:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )


@dp.message(TicketStates.waiting_for_ticket_message)
async def process_ticket_creation(message: Message, state: FSMContext):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        await state.clear()
        return

    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Создание тикета отменено.",
                             reply_markup=get_main_menu() if not is_admin_user else get_admin_main_menu())
        return

    global current_ticket_id
    current_ticket_id += 1
    ticket_id = current_ticket_id

    tickets[ticket_id] = {
        'ticket_id': ticket_id,
        'user_id': user_id,
        'user_name': message.from_user.full_name,
        'username': message.from_user.username,
        'status': 'open',
        'messages': [
            {'from': 'user', 'text': message.text, 'time': datetime.now()}
        ],
        'created_at': datetime.now(),
        'last_update': datetime.now()
    }

    if user_id not in user_tickets:
        user_tickets[user_id] = []
    user_tickets[user_id].append(ticket_id)

    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"📨 Ответить на тикет #{ticket_id}",
        callback_data=f"admin_reply_{ticket_id}"
    )
    builder.button(
        text=f"🔒 Закрыть тикет #{ticket_id}",
        callback_data=f"admin_close_{ticket_id}"
    )
    builder.adjust(1)

    for admin_id in ADMIN_ID:
        try:
            await bot.send_message(
                admin_id,
                f"🎫 *НОВЫЙ ТИКЕТ #{ticket_id}*\n\n"
                f"👤 *Пользователь:* {message.from_user.full_name}\n"
                f"📧 @{message.from_user.username if message.from_user.username else 'нет'}\n"
                f"🆔 ID: `{user_id}`\n\n"
                f"📝 *Сообщение:*\n{message.text}",
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )
        except:
            pass

    await message.answer(
        f"✅ *Тикет #{ticket_id} создан!*\n\n"
        f"📋 *Ваше сообщение:*\n{message.text}\n\n"
        f"⏳ *Статус:* В обработке\n"
        f"📞 Администратор ответит в ближайшее время.\n\n"
        f"🔔 Вы получите уведомление, когда придет ответ.\n"
        f"📋 Просмотреть тикет можно в разделе 'Мои тикеты'",
        parse_mode="Markdown",
        reply_markup=get_main_menu() if not is_admin_user else get_admin_main_menu()
    )

    await state.clear()


@dp.message(F.text == "📋 Мои тикеты")
async def my_tickets(message: Message):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    if user_id not in user_tickets or not user_tickets[user_id]:
        builder = ReplyKeyboardBuilder()
        builder.button(text="🎫 Создать тикет")
        if is_admin_user:
            builder.button(text="👨‍💼 Админ-панель")
        builder.button(text="🏠 Главное меню")
        builder.adjust(2)

        await message.answer(
            "📭 *У вас нет активных тикетов*\n\n"
            "Создайте тикет, если у вас есть вопросы или проблемы.",
            parse_mode="Markdown",
            reply_markup=builder.as_markup(resize_keyboard=True)
        )
        return

    user_ticket_ids = [tid for tid in user_tickets[user_id] if tid in tickets]

    if not user_ticket_ids:
        await message.answer(
            "📭 *У вас нет активных тикетов*",
            parse_mode="Markdown",
            reply_markup=get_main_menu() if not is_admin_user else get_admin_main_menu()
        )
        return

    builder = InlineKeyboardBuilder()
    for ticket_id in sorted(user_ticket_ids, reverse=True)[:10]:
        ticket = tickets[ticket_id]
        status_icon = "🔓" if ticket['status'] == 'open' else "🔒"
        builder.button(
            text=f"{status_icon} Тикет #{ticket_id} ({ticket['status']})",
            callback_data=f"view_ticket_{ticket_id}"
        )

    builder.button(text="🔄 Обновить список", callback_data="refresh_tickets")
    builder.button(text="🎫 Новый тикет", callback_data="create_ticket_quick")
    if is_admin_user:
        builder.button(text="👨‍💼 Админ-панель", callback_data="admin_panel")
    builder.adjust(1)

    await message.answer(
        f"📋 *Ваши тикеты*\n\n"
        f"Найдено тикетов: {len(user_ticket_ids)}\n"
        f"Открытых: {sum(1 for tid in user_ticket_ids if tickets[tid]['status'] == 'open')}\n"
        f"Закрытых: {sum(1 for tid in user_ticket_ids if tickets[tid]['status'] == 'closed')}\n\n"
        f"Выберите тикет для просмотра:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data.startswith('view_ticket_'))
async def view_ticket(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    ticket_id = int(callback_query.data.split('_')[2])
    ticket = tickets.get(ticket_id)

    if not ticket:
        await callback_query.answer("❌ Тикет не найден!")
        return

    is_admin_user = is_admin(user_id)

    # Разрешаем админу просматривать любой тикет (даже свой собственный)
    if not is_admin_user and ticket['user_id'] != user_id:
        await callback_query.answer("❌ У вас нет доступа к этому тикету!")
        return

    response = f"🎫 *Тикет #{ticket_id}*\n"
    response += f"📊 *Статус:* {'🔓 Открыт' if ticket['status'] == 'open' else '🔒 Закрыт'}\n"
    response += f"📅 *Создан:* {ticket['created_at'].strftime('%d.%m.%Y %H:%M')}\n"
    response += f"🔄 *Последнее обновление:* {ticket['last_update'].strftime('%d.%m.%Y %H:%M')}\n"

    if is_admin_user:
        response += f"\n👤 *Пользователь:* {ticket['user_name']}\n"
        response += f"📧 *Username:* @{ticket['username'] if ticket['username'] else 'нет'}\n"
        response += f"🆔 *ID:* `{ticket['user_id']}`\n"

    response += f"\n💬 *История сообщений:*\n\n"

    for msg in ticket['messages']:
        if msg['from'] == 'user':
            if ticket['user_id'] == user_id:
                sender = "👤 Вы"  # Пользователь видит свои сообщения как "Вы"
            else:
                sender = f"👤 {ticket['user_name']}"  # Админ видит имя пользователя
        else:  # admin
            # Определяем, кто из админов написал
            if 'admin_name' in msg:
                sender = f"👨‍💼 {msg['admin_name']}"
            else:
                sender = "👨‍💼 Администратор"

        time_str = msg['time'].strftime('%H:%M')
        response += f"*{sender}* ({time_str}):\n{msg['text']}\n\n"

    builder = InlineKeyboardBuilder()

    if ticket['status'] == 'open':
        if is_admin_user:
            # Админ может отвечать на любой тикет (даже на свой)
            builder.button(
                text="📨 Ответить",
                callback_data=f"admin_reply_{ticket_id}"
            )
            builder.button(
                text="🔒 Закрыть тикет",
                callback_data=f"admin_close_{ticket_id}"
            )
        else:
            # Обычный пользователь
            builder.button(
                text="💬 Ответить",
                callback_data=f"user_reply_{ticket_id}"
            )
            # Пользователь может закрыть только свой тикет
            if ticket['user_id'] == user_id:
                builder.button(
                    text="🔒 Закрыть тикет",
                    callback_data=f"user_close_{ticket_id}"
                )

    # Кнопки возврата
    if is_admin_user:
        if user_id == ticket['user_id']:
            # Если админ смотрит свой собственный тикет
            builder.button(
                text="⬅️ Назад к моим тикетам",
                callback_data="back_to_user_tickets"
            )
        else:
            # Если админ смотрит тикет пользователя
            builder.button(
                text="⬅️ Назад к списку",
                callback_data="back_to_admin_tickets"
            )
    else:
        builder.button(
            text="⬅️ Назад",
            callback_data="back_to_user_tickets"
        )

    builder.adjust(1)

    await callback_query.message.edit_text(
        text=response,
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@dp.callback_query(F.data.startswith('admin_reply_'))
async def admin_reply_to_ticket(callback_query: CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    if not is_admin(user_id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    ticket_id = int(callback_query.data.split('_')[2])
    ticket = tickets.get(ticket_id)

    if not ticket:
        await callback_query.answer("❌ Тикет не найден!")
        return

    if ticket['status'] != 'open':
        await callback_query.answer("❌ Тикет закрыт!")
        return

    await state.set_state(TicketStates.waiting_for_admin_response)
    await state.update_data(ticket_id=ticket_id)

    builder = ReplyKeyboardBuilder()
    builder.button(text="❌ Отмена")
    builder.adjust(1)

    # Определяем, кто получатель
    if ticket['user_id'] == user_id:
        recipient_info = "📝 *Вы отвечаете на свой собственный тикет*"
    else:
        recipient_info = f"👤 *Пользователь:* {ticket['user_name']}"

    await bot.send_message(
        callback_query.from_user.id,
        f"✍️ *Введите ответ для тикета #{ticket_id}:*\n\n"
        f"{recipient_info}\n"
        f"📝 *Последнее сообщение:*\n{ticket['messages'][-1]['text'][:200]}...\n\n"
        f"Для отмены нажмите кнопку ниже:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await callback_query.answer()


@dp.message(TicketStates.waiting_for_admin_response)
async def process_admin_reply(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await state.clear()
        await message.answer("❌ Недостаточно прав!")
        return

    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Ответ отменен.", reply_markup=get_admin_panel_menu())
        return

    data = await state.get_data()
    ticket_id = data['ticket_id']

    if ticket_id in tickets:
        ticket = tickets[ticket_id]

        if ticket['status'] != 'open':
            await message.answer("❌ Тикет закрыт!")
            await state.clear()
            return

        ticket['messages'].append({
            'from': 'admin',
            'text': message.text,
            'time': datetime.now(),
            'admin_name': message.from_user.full_name,
            'admin_id': user_id
        })
        ticket['last_update'] = datetime.now()

        # ИЗМЕНЕНО: Разрешаем админам отвечать на свои тикеты
        # Отправляем уведомление пользователю, если это не сам админ
        if ticket['user_id'] != user_id:
            try:
                user_builder = InlineKeyboardBuilder()
                user_builder.button(
                    text="💬 Ответить",
                    callback_data=f"user_reply_{ticket_id}"
                )
                user_builder.adjust(1)

                await bot.send_message(
                    ticket['user_id'],
                    f"📨 *Ответ от администратора по тикету #{ticket_id}:*\n\n"
                    f"{message.text}\n\n"
                    f"💡 *Для ответа нажмите кнопку ниже:*",
                    parse_mode="Markdown",
                    reply_markup=user_builder.as_markup()
                )
            except:
                pass
        else:
            # Если админ отвечает на свой собственный тикет, уведомляем себя
            await message.answer(f"✅ Ответ сохранен в тикет #{ticket_id}")

        await message.answer(f"✅ Ответ отправлен в тикет #{ticket_id}")

        builder = InlineKeyboardBuilder()
        if ticket['user_id'] == user_id:
            builder.button(
                text="📋 Мои тикеты",
                callback_data="back_to_user_tickets"
            )
        else:
            builder.button(
                text="📋 Открытые тикеты",
                callback_data="back_to_admin_tickets"
            )
        builder.adjust(1)

        await message.answer(
            f"📨 *Ответ на тикет #{ticket_id} отправлен*\n\n"
            f"{'👤 Пользователь уведомлен.' if ticket['user_id'] != user_id else '📝 Ответ сохранен в вашем тикете.'}",
            parse_mode="Markdown",
            reply_markup=builder.as_markup()
        )

    await state.clear()


@dp.callback_query(F.data.startswith('user_reply_'))
async def user_reply_to_ticket(callback_query: CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    ticket_id = int(callback_query.data.split('_')[2])
    ticket = tickets.get(ticket_id)

    if not ticket:
        await callback_query.answer("❌ Тикет не найден!")
        return

    if ticket['user_id'] != callback_query.from_user.id:
        await callback_query.answer("❌ У вас нет доступа к этому тикету!")
        return

    if ticket['status'] != 'open':
        await callback_query.answer("❌ Тикет закрыт!")
        return

    await state.set_state(TicketStates.waiting_for_user_reply)
    await state.update_data(ticket_id=ticket_id)

    builder = ReplyKeyboardBuilder()
    builder.button(text="❌ Отмена")
    builder.adjust(1)

    await bot.send_message(
        callback_query.from_user.id,
        f"✍️ *Введите ваш ответ для тикета #{ticket_id}:*\n\n"
        f"💡 *Администратор получит уведомление о вашем ответе.*\n\n"
        f"Для отмены нажмите кнопку ниже:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await callback_query.answer()


@dp.message(TicketStates.waiting_for_user_reply)
async def process_user_reply(message: Message, state: FSMContext):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        await state.clear()
        return

    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Ответ отменен.",
                             reply_markup=get_main_menu() if not is_admin_user else get_admin_main_menu())
        return

    data = await state.get_data()
    ticket_id = data['ticket_id']

    if ticket_id in tickets:
        ticket = tickets[ticket_id]

        if ticket['status'] != 'open':
            await message.answer("❌ Тикет закрыт!")
            await state.clear()
            return

        if ticket['user_id'] != message.from_user.id:
            await message.answer("❌ У вас нет доступа к этому тикету!")
            await state.clear()
            return

        ticket['messages'].append({
            'from': 'user',
            'text': message.text,
            'time': datetime.now()
        })
        ticket['last_update'] = datetime.now()

        admin_builder = InlineKeyboardBuilder()
        admin_builder.button(
            text=f"📨 Ответить на тикет #{ticket_id}",
            callback_data=f"admin_reply_{ticket_id}"
        )
        admin_builder.adjust(1)

        for admin_id in ADMIN_ID:
            try:
                await bot.send_message(
                    admin_id,
                    f"🔄 *ОТВЕТ В ТИКЕТ #{ticket_id}*\n\n"
                    f"👤 *Пользователь:* {message.from_user.full_name}\n"
                    f"📧 @{message.from_user.username if message.from_user.username else 'нет'}\n"
                    f"🆔 ID: `{message.from_user.id}`\n\n"
                    f"📝 *Сообщение:*\n{message.text}",
                    parse_mode="Markdown",
                    reply_markup=admin_builder.as_markup()
                )
            except:
                pass

        await message.answer(f"✅ Ваш ответ отправлен администратору в тикет #{ticket_id}")

    await state.clear()


@dp.callback_query(F.data.startswith('admin_close_'))
async def admin_close_ticket(callback_query: CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    if not is_admin(user_id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    current_state = await state.get_state()
    if current_state:
        await state.clear()

    ticket_id = int(callback_query.data.split('_')[2])

    if ticket_id in tickets:
        tickets[ticket_id]['status'] = 'closed'
        tickets[ticket_id]['last_update'] = datetime.now()
        tickets[ticket_id]['closed_by'] = callback_query.from_user.id
        tickets[ticket_id]['closed_at'] = datetime.now()

        user_id = tickets[ticket_id]['user_id']
        try:
            await bot.send_message(
                user_id,
                f"🔒 *Тикет #{ticket_id} был закрыт администратором.*\n\n"
                f"Если проблема не решена, создайте новый тикет.",
                parse_mode="Markdown"
            )
        except:
            pass

        await callback_query.answer(f"✅ Тикет #{ticket_id} закрыт!")

        await callback_query.message.answer(
            f"✅ Тикет #{ticket_id} успешно закрыт!",
            reply_markup=get_admin_panel_menu()
        )
    else:
        await callback_query.answer("❌ Тикет не найден!")


@dp.callback_query(F.data.startswith('user_close_'))
async def user_close_ticket(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    ticket_id = int(callback_query.data.split('_')[2])
    ticket = tickets.get(ticket_id)

    if not ticket:
        await callback_query.answer("❌ Тикет не найден!")
        return

    if ticket['user_id'] != callback_query.from_user.id:
        await callback_query.answer("❌ У вас нет доступа к этому тикету!")
        return

    if ticket['status'] != 'open':
        await callback_query.answer("❌ Тикет уже закрыт!")
        return

    tickets[ticket_id]['status'] = 'closed'
    tickets[ticket_id]['last_update'] = datetime.now()

    for admin_id in ADMIN_ID:
        try:
            await bot.send_message(
                admin_id,
                f"🔒 *Тикет #{ticket_id} был закрыт пользователем.*\n\n"
                f"👤 Пользователь: {ticket['user_name']}\n"
                f"📧 @{ticket['username'] if ticket['username'] else 'нет'}\n"
                f"🆔 ID: `{ticket['user_id']}`",
                parse_mode="Markdown"
            )
        except:
            pass

    await callback_query.answer(f"✅ Тикет #{ticket_id} закрыт!")

    user_id = callback_query.from_user.id
    user_ticket_ids = [tid for tid in user_tickets.get(user_id, []) if tid in tickets]

    builder = InlineKeyboardBuilder()

    if user_ticket_ids:
        for t_id in sorted(user_ticket_ids, reverse=True)[:10]:
            t = tickets[t_id]
            status_icon = "🔓" if t['status'] == 'open' else "🔒"
            builder.button(
                text=f"{status_icon} Тикет #{t_id} ({t['status']})",
                callback_data=f"view_ticket_{t_id}"
            )

        builder.adjust(1)

        await callback_query.message.edit_text(
            text=f"✅ Тикет #{ticket_id} успешно закрыт.\n\n"
                 f"📋 Ваши тикеты:",
            reply_markup=builder.as_markup()
        )
    else:
        await callback_query.message.edit_text(
            text=f"✅ Тикет #{ticket_id} успешно закрыт.\n\n"
                 f"📭 У вас больше нет активных тикетов."
        )


@dp.callback_query(F.data == "back_to_user_tickets")
async def back_to_user_tickets(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    user_ticket_ids = [tid for tid in user_tickets.get(user_id, []) if tid in tickets]

    if not user_ticket_ids:
        await callback_query.message.edit_text(
            text="📭 У вас нет активных тикетов.",
            reply_markup=InlineKeyboardBuilder()
            .button(text="🎫 Создать тикет", callback_data="create_ticket_quick")
            .as_markup()
        )
        return

    builder = InlineKeyboardBuilder()

    for ticket_id in sorted(user_ticket_ids, reverse=True)[:10]:
        ticket = tickets[ticket_id]
        status_icon = "🔓" if ticket['status'] == 'open' else "🔒"
        builder.button(
            text=f"{status_icon} Тикет #{ticket_id} ({ticket['status']})",
            callback_data=f"view_ticket_{ticket_id}"
        )

    builder.button(text="🔄 Обновить список", callback_data="refresh_tickets")
    builder.button(text="🎫 Новый тикет", callback_data="create_ticket_quick")
    builder.adjust(1)

    await callback_query.message.edit_text(
        text="📋 Ваши тикеты:\n\n"
             f"Найдено тикетов: {len(user_ticket_ids)}\n"
             f"Открытых: {sum(1 for tid in user_ticket_ids if tickets[tid]['status'] == 'open')}",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@dp.callback_query(F.data == "back_to_admin_tickets")
async def back_to_admin_tickets(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if not is_admin(user_id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    open_tickets_count = sum(1 for t in tickets.values() if t['status'] == 'open')

    if open_tickets_count == 0:
        await callback_query.message.edit_text(
            text="✅ Нет открытых тикетов.",
            reply_markup=InlineKeyboardBuilder()
            .button(text="📋 Все тикеты", callback_data="admin_all_tickets")
            .as_markup()
        )
        return

    builder = InlineKeyboardBuilder()

    for ticket_id, ticket in tickets.items():
        if ticket['status'] == 'open':
            builder.button(
                text=f"🎫 #{ticket_id} - {ticket['user_name']}",
                callback_data=f"view_ticket_{ticket_id}"
            )

    builder.adjust(1)

    await callback_query.message.edit_text(
        text=f"📋 Открытые тикеты ({open_tickets_count}):",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@dp.message(F.text == "📋 Открытые тикеты")
async def admin_open_tickets(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта функция доступна только администраторам.")
        return

    open_tickets_count = sum(1 for t in tickets.values() if t['status'] == 'open')

    if open_tickets_count == 0:
        await message.answer(
            "✅ *Нет открытых тикетов*\n\n"
            "Все тикеты обработаны!",
            parse_mode="Markdown"
        )
        return

    builder = InlineKeyboardBuilder()

    for ticket_id, ticket in tickets.items():
        if ticket['status'] == 'open':
            builder.button(
                text=f"🎫 #{ticket_id} - {ticket['user_name']}",
                callback_data=f"view_ticket_{ticket_id}"
            )

    builder.adjust(1)

    await message.answer(
        f"📋 *Открытые тикеты ({open_tickets_count}):*",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data == "create_ticket_quick")
async def create_ticket_quick_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    await callback_query.message.answer(
        "🎫 *Создание тикета*\n\n"
        "Опишите вашу проблему или вопрос:",
        parse_mode="Markdown"
    )
    context = FSMContext(storage=storage, key="user", chat=callback_query.from_user.id,
                         user=callback_query.from_user.id)
    await context.set_state(TicketStates.waiting_for_ticket_message)
    await callback_query.answer()


@dp.callback_query(F.data == "refresh_tickets")
async def refresh_tickets_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    if is_admin_user:
        await back_to_admin_tickets(callback_query)
    else:
        await back_to_user_tickets(callback_query)

    await callback_query.answer("✅ Список обновлен!")


@dp.message(F.text == "📊 Статистика тикетов")
async def ticket_statistics(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта функция доступна только администраторам.")
        return

    total_tickets = len(tickets)
    open_tickets = sum(1 for t in tickets.values() if t['status'] == 'open')
    closed_tickets = total_tickets - open_tickets

    week_ago = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tickets_last_week = [t for t in tickets.values() if t['created_at'] >= week_ago]
    tickets_today = [t for t in tickets.values() if t['created_at'].date() == datetime.now().date()]

    stats_text = (
        f"📊 *Статистика тикетов*\n\n"
        f"🎫 Всего тикетов: {total_tickets}\n"
        f"🔓 Открытых: {open_tickets}\n"
        f"🔒 Закрытых: {closed_tickets}\n\n"
        f"📈 *Активность:*\n"
        f"• За сегодня: {len(tickets_today)}\n"
        f"• За неделю: {len(tickets_last_week)}\n"
        f"• Среднее время ответа: 2-4 часа\n\n"
    )

    if total_tickets > 0:
        recent_tickets = sorted(tickets.items(), key=lambda x: x[1]['created_at'], reverse=True)[:5]
        stats_text += "\n*Последние тикеты:*\n"
        for ticket_id, ticket in recent_tickets:
            status_icon = "🔓" if ticket['status'] == 'open' else "🔒"
            stats_text += f"{status_icon} #{ticket_id} - {ticket['user_name']}\n"

    await message.answer(stats_text, parse_mode="Markdown")


# ========== 📊 СТАТИСТИКА (Админ) ==========

@dp.message(F.text == "📊 Статистика")
async def admin_statistics(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта функция доступна только администраторам.")
        return

    total_users = len(referral_stats)
    today = datetime.now().date()
    new_today = len([uid for uid, stats in referral_stats.items()
                     if 'created_at' in stats and stats['created_at'].date() == today])

    all_orders = []
    for orders in user_orders.values():
        all_orders.extend(orders)

    total_orders = len(all_orders)
    orders_today = len([o for o in all_orders if o['created_at'].date() == today])
    total_revenue = sum(o['total_price'] for o in all_orders if o['status'] != 'cancelled')

    total_balance = sum(bal['balance'] for bal in user_balances.values())
    total_deposits = sum(
        sum(t['amount'] for t in bal['transactions'] if t['type'] == 'deposit' and t['status'] == 'completed')
        for bal in user_balances.values()
    )

    total_tickets = len(tickets)
    open_tickets = sum(1 for t in tickets.values() if t['status'] == 'open')

    total_referrals = sum(stats['count'] for stats in referral_stats.values())
    total_ref_earned = sum(stats['earned'] for stats in referral_stats.values())

    catalog_size = len(catalog)
    total_quantity = sum(p.get('quantity', 0) for p in catalog.values())

    # Статистика по реферальным товарам
    referral_products_count = sum(1 for p in catalog.values() if p.get('referral_enabled', False))
    referral_products_quantity = sum(p.get('quantity', 0) for p in catalog.values() if p.get('referral_enabled', False))

    stats_text = (
        f"📊 *ОБЩАЯ СТАТИСТИКА БОТА*\n\n"

        f"👥 *Пользователи:*\n"
        f"• Всего: {total_users}\n"
        f"• Новых сегодня: {new_today}\n"
        f"• С балансом > 0: {len([uid for uid in user_balances.keys() if user_balances[uid]['balance'] > 0])}\n\n"

        f"🛒 *Заказы:*\n"
        f"• Всего: {total_orders}\n"
        f"• Сегодня: {orders_today}\n"
        f"• Выручка: {format_currency(total_revenue)} ₽\n"
        f"• Средний чек: {format_currency(total_revenue / total_orders) if total_orders > 0 else Decimal('0.00')} ₽\n\n"

        f"📦 *Каталог:*\n"
        f"• Товаров в каталоге: {catalog_size}\n"
        f"• Всего на складе: {total_quantity} шт.\n"
        f"• Товаров с реферальной программой: {referral_products_count}\n"
        f"• На складе с реферальной программой: {referral_products_quantity} шт.\n\n"

        f"💰 *Финансы:*\n"
        f"• Общий баланс всех: {format_currency(total_balance)} ₽\n"
        f"• Всего пополнений: {format_currency(total_deposits)} ₽\n\n"

        f"🎫 *Тикеты:*\n"
        f"• Всего: {total_tickets}\n"
        f"• Открытых: {open_tickets}\n"
        f"• Закрытых: {total_tickets - open_tickets}\n\n"

        f"👥 *Рефералы:*\n"
        f"• Всего рефералов: {total_referrals}\n"
        f"• Всего бонусов: {format_currency(total_ref_earned)} ₽\n\n"

        f"📈 *Активность за 24 часа:*\n"
        f"• Новые пользователи: {new_today}\n"
        f"• Новые заказы: {orders_today}\n"
        f"• Новые тикеты: {len([t for t_id, t in tickets.items() if (datetime.now() - t['created_at']).days < 1])}"
    )

    await message.answer(stats_text, parse_mode="Markdown")


# ========== 👥 РЕФЕРАЛЫ (Админ) ==========

@dp.message(F.text == "👥 Рефералы (Админ)")
async def admin_referral_menu(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта функция доступна только администраторам.")
        return

    total_users = len(referral_stats)
    total_referrals = sum(stats['count'] for stats in referral_stats.values())
    total_earned = sum(stats['earned'] for stats in referral_stats.values())

    today = datetime.now().date()
    today_referrals = 0
    for stats in referral_stats.values():
        if 'created_at' in stats and stats['created_at'].date() == today:
            today_referrals += 1

    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Общая статистика", callback_data="admin_referral_stats")
    builder.button(text="👥 Топ рефералов", callback_data="admin_referral_top")
    builder.button(text="📋 Список всех", callback_data="admin_referral_list")
    builder.button(text="🛒 Товары с реферальной программой", callback_data="admin_referral_products")
    builder.adjust(2)

    await message.answer(
        f"👥 *Управление реферальной системой*\n\n"
        f"📊 *Статистика:*\n"
        f"• Участников: {total_users}\n"
        f"• Всего рефералов: {total_referrals}\n"
        f"• Всего баллов: {format_currency(total_earned)} ₽\n"
        f"• Новых сегодня: {today_referrals}\n\n"
        f"Выберите действие:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data == "admin_referral_stats")
async def admin_referral_stats_handler(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    total_users = len(referral_stats)
    total_referrals = sum(stats['count'] for stats in referral_stats.values())
    total_earned = sum(stats['earned'] for stats in referral_stats.values())

    top_referrers = sorted(
        [(uid, stats['count'], stats['earned']) for uid, stats in referral_stats.items() if stats['count'] > 0],
        key=lambda x: x[1],
        reverse=True
    )[:5]

    # Статистика по реферальным товарам
    referral_products = []
    referral_products_sales = {}

    for user_id, orders in user_orders.items():
        for order in orders:
            product = catalog.get(order['product_id'])
            if product and product.get('referral_enabled', False):
                if product['name'] not in referral_products_sales:
                    referral_products_sales[product['name']] = {
                        'count': 0,
                        'revenue': Decimal('0.00')
                    }
                referral_products_sales[product['name']]['count'] += order['quantity']
                referral_products_sales[product['name']]['revenue'] += order['total_price']

    stats_text = f"📊 *ДЕТАЛЬНАЯ СТАТИСТИКА РЕФЕРАЛОВ*\n\n"
    stats_text += f"👥 *Общее:*\n"
    stats_text += f"• Участников: {total_users}\n"
    stats_text += f"• Всего рефералов: {total_referrals}\n"
    stats_text += f"• Всего бонусов: {format_currency(total_earned)} ₽\n\n"

    if top_referrers:
        stats_text += "🏆 *ТОП-5 РЕФЕРЕРОВ:*\n"
        for i, (uid, count, earned) in enumerate(top_referrers, 1):
            stats_text += f"{i}. ID{uid}: {count} чел. | {format_currency(earned)} ₽\n"

    if referral_products_sales:
        stats_text += "\n🛒 *ПРОДАЖИ ТОВАРОВ С РЕФЕРАЛЬНОЙ ПРОГРАММОЙ:*\n"
        for product_name, sales in referral_products_sales.items():
            stats_text += f"• {product_name}: {sales['count']} шт. | {format_currency(sales['revenue'])} ₽\n"

    await callback_query.message.answer(stats_text, parse_mode="Markdown")
    await callback_query.answer()


@dp.callback_query(F.data == "admin_referral_top")
async def admin_referral_top_handler(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    top_users = []
    for user_id, stats in referral_stats.items():
        if stats['count'] > 0:
            top_users.append((user_id, stats['count'], stats['earned']))

    top_users.sort(key=lambda x: x[1], reverse=True)

    if not top_users:
        await callback_query.message.answer("🏆 Пока нет активных рефералов.")
        return

    top_list = "🏆 *ТОП-20 РЕФЕРАЛОВ*\n\n"
    for i, (user_id, count, earned) in enumerate(top_users[:20], 1):
        code = referral_links.get(user_id, "N/A")
        top_list += f"{i}. ID{user_id} (код: `{code}`)\n"
        top_list += f"   👥 {count} чел. | 💰 {format_currency(earned)} ₽\n"
        top_list += f"   🔗 t.me/{BOT_USERNAME}?start={code}\n\n"

    await callback_query.message.answer(
        top_list,
        parse_mode="Markdown"
    )
    await callback_query.answer()


@dp.callback_query(F.data == "admin_referral_list")
async def admin_referral_list_handler(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    if not referral_stats:
        await callback_query.message.answer("📭 Нет участников реферальной системы.")
        return

    all_users = []
    for user_id, stats in referral_stats.items():
        all_users.append((user_id, stats['count'], stats['earned'], stats['created_at']))

    all_users.sort(key=lambda x: x[3], reverse=True)

    user_list = "📋 *СПИСОК ВСЕХ УЧАСТНИКОВ*\n\n"
    for i, (user_id, count, earned, created_at) in enumerate(all_users[:50], 1):
        code = referral_links.get(user_id, "N/A")
        user_list += f"{i}. ID{user_id} (код: `{code}`)\n"
        user_list += f"   👥 {count} чел. | 💰 {format_currency(earned)} ₽\n"
        user_list += f"   📅 {created_at.strftime('%d.%m.%Y')}\n\n"

    if len(all_users) > 50:
        user_list += f"\n... и еще {len(all_users) - 50} участников"

    await callback_query.message.answer(
        user_list,
        parse_mode="Markdown"
    )
    await callback_query.answer()


@dp.callback_query(F.data == "admin_referral_products")
async def admin_referral_products_handler(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    referral_products = []
    for product_id, product in catalog.items():
        if product.get('referral_enabled', False):
            referral_bonus = product.get('referral_bonus', bot_settings['referral_bonus'])
            referral_products.append((product_id, product, referral_bonus))

    if not referral_products:
        await callback_query.message.answer("🛒 Нет товаров с реферальной программой.")
        return

    products_text = "🛒 *ТОВАРЫ С РЕФЕРАЛЬНОЙ ПРОГРАММОЙ*\n\n"
    for product_id, product, referral_bonus in referral_products:
        products_text += f"📦 *{product['name']}* (ID: {product_id})\n"
        products_text += f"💰 Цена: {format_currency(product['price'])} ₽\n"
        products_text += f"👥 Реферальный бонус: {format_currency(referral_bonus)} ₽\n"
        products_text += f"📊 Количество: {product.get('quantity', 0)} шт.\n"
        products_text += f"📝 {product['description'][:100]}...\n\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика продаж", callback_data="referral_products_stats")
    builder.button(text="⬅️ Назад", callback_data="admin_referrals")
    builder.adjust(1)

    await callback_query.message.answer(
        products_text,
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@dp.callback_query(F.data == "referral_products_stats")
async def referral_products_stats_handler(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    referral_products_stats = {}
    total_referral_revenue = Decimal('0.00')
    total_referral_bonuses = Decimal('0.00')

    for user_id, orders in user_orders.items():
        for order in orders:
            product = catalog.get(order['product_id'])
            if product and product.get('referral_enabled', False):
                product_name = product['name']
                if product_name not in referral_products_stats:
                    referral_products_stats[product_name] = {
                        'sales': 0,
                        'revenue': Decimal('0.00'),
                        'bonuses': Decimal('0.00')
                    }

                referral_products_stats[product_name]['sales'] += order['quantity']
                referral_products_stats[product_name]['revenue'] += order['total_price']

                # Расчет бонусов
                referral_bonus = product.get('referral_bonus', bot_settings['referral_bonus'])
                if user_id in user_referrer:
                    referral_products_stats[product_name]['bonuses'] += referral_bonus
                    total_referral_bonuses += referral_bonus

                total_referral_revenue += order['total_price']

    if not referral_products_stats:
        await callback_query.message.answer("📊 Нет статистики по товарам с реферальной программой.")
        return

    stats_text = "📊 *СТАТИСТИКА ПО ТОВАРАМ С РЕФЕРАЛЬНОЙ ПРОГРАММОЙ*\n\n"
    stats_text += f"💰 *Общая выручка:* {format_currency(total_referral_revenue)} ₽\n"
    stats_text += f"👥 *Всего выплачено бонусов:* {format_currency(total_referral_bonuses)} ₽\n\n"
    stats_text += "📈 *Детальная статистика по товарам:*\n\n"

    for product_name, stats in referral_products_stats.items():
        stats_text += f"📦 *{product_name}*\n"
        stats_text += f"   🛒 Продано: {stats['sales']} шт.\n"
        stats_text += f"   💰 Выручка: {format_currency(stats['revenue'])} ₽\n"
        stats_text += f"   👥 Выплачено бонусов: {format_currency(stats['bonuses'])} ₽\n\n"

    await callback_query.message.answer(stats_text, parse_mode="Markdown")
    await callback_query.answer()


# ========== ⚙️ НАСТРОЙКИ (Админ) ==========

@dp.message(F.text == "⚙️ Настройки")
async def settings_menu(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта функция доступна только администраторам.")
        return

    settings_text = (
        f"⚙️ *Настройки бота*\n\n"
        f"📊 *Текущие настройки:*\n"
        f"• Техобслуживание: {'✅ Включено' if bot_settings['maintenance'] else '❌ Выключено'}\n"
        f"• Сообщение при техобслуживании: {bot_settings['maintenance_message']}\n"
        f"• Бонус за регистрацию: {format_currency(bot_settings['welcome_bonus'])} ₽\n"
        f"• Бонус за реферала: {format_currency(bot_settings['referral_bonus'])} ₽\n\n"
        f"Выберите настройку для изменения:"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🔧 Техобслуживание", callback_data="toggle_maintenance")
    builder.button(text="🎁 Бонус за регистрацию", callback_data="change_welcome_bonus")
    builder.button(text="👥 Бонус за реферала", callback_data="change_referral_bonus")
    builder.button(text="📝 Сообщение техобслуживания", callback_data="change_maintenance_message")
    builder.button(text="⬅️ Назад", callback_data="back_to_admin_panel")
    builder.adjust(2)

    await message.answer(settings_text, parse_mode="Markdown", reply_markup=builder.as_markup())


@dp.callback_query(F.data == "toggle_maintenance")
async def toggle_maintenance(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    bot_settings['maintenance'] = not bot_settings['maintenance']
    status = "включено" if bot_settings['maintenance'] else "выключено"

    await callback_query.message.edit_text(
        f"✅ Техобслуживание {status}!\n\n"
        f"Текущий статус: {'🔧 ВКЛЮЧЕНО' if bot_settings['maintenance'] else '✅ ВЫКЛЮЧЕНО'}",
        reply_markup=InlineKeyboardBuilder()
        .button(text="⚙️ Настройки", callback_data="back_to_settings")
        .as_markup()
    )
    await callback_query.answer()


@dp.callback_query(F.data == "change_welcome_bonus")
async def change_welcome_bonus(callback_query: CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    await state.set_state(AdminSettingsStates.waiting_for_welcome_bonus)
    await callback_query.message.answer(
        f"🎁 *Изменение бонуса за регистрацию*\n\n"
        f"Текущий бонус: {format_currency(bot_settings['welcome_bonus'])} ₽\n\n"
        f"Введите новую сумму бонуса (в рублях):\n"
        f"Пример: 100 или 150.50\n\n"
        f"Для отмены напишите /cancel"
    )
    await callback_query.answer()


@dp.message(AdminSettingsStates.waiting_for_welcome_bonus)
async def process_welcome_bonus(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Изменение бонуса отменено.", reply_markup=get_admin_panel_menu())
        return

    try:
        new_bonus = Decimal(message.text.replace(',', '.'))
        if new_bonus < 0:
            await message.answer("❌ Бонус не может быть отрицательным!")
            return
        bot_settings['welcome_bonus'] = new_bonus.quantize(Decimal('0.01'))
        await message.answer(f"✅ Бонус за регистрацию изменен на {format_currency(new_bonus)} ₽",
                             reply_markup=get_admin_panel_menu())
    except:
        await message.answer("❌ Введите число! Пример: 100 или 150.50")
        return

    await state.clear()


@dp.callback_query(F.data == "change_referral_bonus")
async def change_referral_bonus(callback_query: CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    await state.set_state(AdminSettingsStates.waiting_for_referral_bonus)
    await callback_query.message.answer(
        f"👥 *Изменение бонуса за реферала*\n\n"
        f"Текущий бонус: {format_currency(bot_settings['referral_bonus'])} ₽\n\n"
        f"Введите новую сумму бонуса (в рублях):\n"
        f"Пример: 50 или 75.25\n\n"
        f"Для отмены напишите /cancel"
    )
    await callback_query.answer()


@dp.message(AdminSettingsStates.waiting_for_referral_bonus)
async def process_referral_bonus(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Изменение бонуса отменено.", reply_markup=get_admin_panel_menu())
        return

    try:
        new_bonus = Decimal(message.text.replace(',', '.'))
        if new_bonus < 0:
            await message.answer("❌ Бонус не может быть отрицательным!")
            return
        bot_settings['referral_bonus'] = new_bonus.quantize(Decimal('0.01'))
        await message.answer(f"✅ Бонус за реферала изменен на {format_currency(new_bonus)} ₽",
                             reply_markup=get_admin_panel_menu())
    except:
        await message.answer("❌ Введите число! Пример: 50 или 75.25")
        return

    await state.clear()


@dp.callback_query(F.data == "change_maintenance_message")
async def change_maintenance_message(callback_query: CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    await state.set_state(AdminSettingsStates.waiting_for_maintenance_message)
    await callback_query.message.answer(
        f"📝 *Изменение сообщения техобслуживания*\n\n"
        f"Текущее сообщение: {bot_settings['maintenance_message']}\n\n"
        f"Введите новое сообщение:\n\n"
        f"Для отмены напишите /cancel"
    )
    await callback_query.answer()


@dp.message(AdminSettingsStates.waiting_for_maintenance_message)
async def process_maintenance_message(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Изменение сообщения отменено.", reply_markup=get_admin_panel_menu())
        return

    bot_settings['maintenance_message'] = message.text
    await message.answer(f"✅ Сообщение техобслуживания изменено на:\n\n{message.text}",
                         reply_markup=get_admin_panel_menu())
    await state.clear()


@dp.callback_query(F.data == "back_to_settings")
async def back_to_settings(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    settings_text = (
        f"⚙️ *Настройки бота*\n\n"
        f"📊 *Текущие настройки:*\n"
        f"• Техобслуживание: {'✅ Включено' if bot_settings['maintenance'] else '❌ Выключено'}\n"
        f"• Сообщение при техобслуживании: {bot_settings['maintenance_message']}\n"
        f"• Бонус за регистрацию: {format_currency(bot_settings['welcome_bonus'])} ₽\n"
        f"• Бонус за реферала: {format_currency(bot_settings['referral_bonus'])} ₽\n\n"
        f"Выберите настройку для изменения:"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🔧 Техобслуживание", callback_data="toggle_maintenance")
    builder.button(text="🎁 Бонус за регистрацию", callback_data="change_welcome_bonus")
    builder.button(text="👥 Бонус за реферала", callback_data="change_referral_bonus")
    builder.button(text="📝 Сообщение техобслуживания", callback_data="change_maintenance_message")
    builder.button(text="⬅️ Назад", callback_data="back_to_admin_panel")
    builder.adjust(2)

    await callback_query.message.edit_text(
        settings_text,
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@dp.callback_query(F.data == "back_to_admin_panel")
async def back_to_admin_panel_menu(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    await callback_query.message.edit_text(
        "👨‍💼 *Панель администратора*\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardBuilder()
        .button(text="📊 Статистика", callback_data="admin_stats")
        .button(text="🎫 Поддержка (Админ)", callback_data="admin_tickets")
        .button(text="💰 Управление балансами", callback_data="admin_balances")
        .button(text="👥 Рефералы (Админ)", callback_data="admin_referrals")
        .button(text="📢 Рассылка", callback_data="admin_broadcast")
        .button(text="⚙️ Настройки", callback_data="admin_settings")
        .button(text="🛒 Управление каталогом", callback_data="admin_catalog")
        .button(text="🏠 Главное меню", callback_data="back_to_main_menu")
        .adjust(2)
        .as_markup()
    )
    await callback_query.answer()


# ========== 📢 РАССЫЛКА (Админ) ==========

@dp.message(F.text == "📢 Рассылка")
async def broadcast_menu_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта функция доступна только администраторам.")
        return

    total_users = len(referral_stats)

    broadcast_text = (
        f"📢 *Управление рассылками*\n\n"
        f"📊 *Статистика пользователей:*\n"
        f"• Всего пользователей: {total_users}\n\n"
        f"Выберите действие:"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Всем пользователям", callback_data="broadcast_all")
    builder.adjust(1)

    await message.answer(broadcast_text, parse_mode="Markdown", reply_markup=builder.as_markup())


@dp.callback_query(F.data == "broadcast_all")
async def broadcast_all_users(callback_query: CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    await state.set_state(BroadcastStates.waiting_for_broadcast_message)

    await callback_query.message.answer(
        "📢 *Создание рассылки*\n\n"
        "Введите сообщение для рассылки всем пользователям:\n\n"
        "💡 *Можно использовать:*\n"
        "• Текст\n"
        "• Форматирование Markdown\n"
        "• Эмодзи\n\n"
        "❌ *Для отмены напишите:* /cancel",
        parse_mode="Markdown"
    )
    await callback_query.answer()


@dp.message(BroadcastStates.waiting_for_broadcast_message)
async def process_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Рассылка отменена.", reply_markup=get_admin_panel_menu())
        return

    all_users = list(referral_stats.keys())

    if not all_users:
        await message.answer("❌ Нет пользователей для рассылки.")
        await state.clear()
        return

    sent_count = 0
    failed_count = 0

    await message.answer(f"📤 Начинаю рассылку для {len(all_users)} пользователей...")

    for user_id in all_users:
        try:
            await bot.send_message(
                user_id,
                f"📢 *Рассылка от администратора*\n\n{message.text}",
                parse_mode="Markdown"
            )
            sent_count += 1
            await asyncio.sleep(0.05)
        except:
            failed_count += 1

    await message.answer(
        f"✅ *Рассылка завершена!*\n\n"
        f"📊 *Результаты:*\n"
        f"• Отправлено: {sent_count}\n"
        f"• Не отправлено: {failed_count}\n"
        f"• Всего пользователей: {len(all_users)}",
        parse_mode="Markdown",
        reply_markup=get_admin_panel_menu()
    )

    await state.clear()


# ========== 💰 УПРАВЛЕНИЕ БАЛАНСАМИ ==========

@dp.message(F.text == "💰 Управление балансами")
async def manage_balances_menu(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта функция доступна только администраторам.")
        return

    total_users = len(user_balances)
    total_balance = sum(bal['balance'] for bal in user_balances.values())

    balance_text = (
        f"💰 *Управление балансами*\n\n"
        f"📊 *Статистика:*\n"
        f"• Пользователей с балансом: {total_users}\n"
        f"• Общий баланс всех: {format_currency(total_balance)} ₽\n\n"
        f"Выберите действие:"
    )

    builder = ReplyKeyboardBuilder()
    builder.button(text="➕ Начислить баланс")
    builder.button(text="➖ Списать баланс")
    builder.button(text="📋 Список пользователей")
    builder.button(text="👨‍💼 Админ-панель")
    builder.adjust(2)

    await message.answer(
        balance_text,
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )


@dp.message(F.text == "➕ Начислить баланс")
async def handle_add_balance(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта функция доступна только администраторам.")
        return

    await state.set_state(AdminBalanceStates.waiting_for_user_id)
    await state.update_data(is_removal=False)

    await message.answer(
        "💰 *Начисление баланса*\n\n"
        "Введите ID пользователя:\n\n"
        "💡 *ID можно получить из статистики или тикетов*\n\n"
        "Для отмены напишите /cancel",
        parse_mode="Markdown"
    )


@dp.message(F.text == "➖ Списать баланс")
async def handle_remove_balance(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта функция доступна только администраторам.")
        return

    await state.set_state(AdminBalanceStates.waiting_for_user_id)
    await state.update_data(is_removal=True)

    await message.answer(
        "⚠️ *Списание баланса*\n\n"
        "Введите ID пользователя для списания средств:\n\n"
        "💡 *ID можно получить из статистики или тикетов*\n\n"
        "Для отмены напишите /cancel",
        parse_mode="Markdown"
    )


@dp.message(F.text == "📋 Список пользователей")
async def handle_list_users_balance(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта функция доступна только администраторам.")
        return

    if not user_balances:
        await message.answer("📭 Нет пользователей с балансом.")
        return

    sorted_users = sorted(
        user_balances.items(),
        key=lambda x: x[1]['balance'],
        reverse=True
    )

    user_list = "📋 *ПОЛЬЗОВАТЕЛИ С БАЛАНСОМ*\n\n"
    for i, (user_id, data) in enumerate(sorted_users[:50], 1):
        username = ""
        # Ищем имя пользователя
        for ticket in tickets.values():
            if ticket['user_id'] == user_id:
                username = ticket['user_name']
                break

        user_list += f"{i}. ID: `{user_id}`\n"
        if username:
            user_list += f"   👤 Имя: {username}\n"
        user_list += f"   💰 Баланс: {format_currency(data['balance'])} ₽\n"
        if 'created_at' in data:
            user_list += f"   📅 Регистрация: {data['created_at'].strftime('%d.%m.%Y')}\n"
        user_list += f"   📊 Транзакций: {len(data['transactions'])}\n\n"

    if len(sorted_users) > 50:
        user_list += f"\n... и еще {len(sorted_users) - 50} пользователей"

    await message.answer(user_list, parse_mode="Markdown")


@dp.message(AdminBalanceStates.waiting_for_user_id)
async def process_admin_user_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        await message.answer("❌ Недостаточно прав!")
        return

    if message.text == "/cancel" or message.text.lower() == "отмена":
        await state.clear()
        await message.answer(
            "❌ Операция отменена.",
            reply_markup=get_admin_panel_menu()
        )
        return

    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer(
            "❌ Введите числовой ID пользователя!\n\n"
            "Попробуйте еще раз или напишите /cancel для отмены"
        )
        return  # Не очищаем state, даем возможность исправить

    # Проверяем, есть ли пользователь в системе
    if user_id not in referral_stats and user_id not in user_balances:
        # Спрашиваем, создать ли нового пользователя
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Да, создать", callback_data=f"create_user_{user_id}")
        builder.button(text="❌ Нет, отмена", callback_data="cancel_create_user")
        builder.adjust(2)

        await message.answer(
            f"❌ Пользователь с ID {user_id} не найден в системе!\n\n"
            f"Хотите создать нового пользователя с этим ID?",
            reply_markup=builder.as_markup()
        )
        return

    # Создаем запись о пользователе, если её нет
    if user_id not in user_balances:
        user_balances[user_id] = {
            'balance': Decimal('0.00'),
            'transactions': [],
            'created_at': datetime.now()
        }

    await state.update_data(user_id=user_id)
    await state.set_state(AdminBalanceStates.waiting_for_amount)

    data = await state.get_data()
    is_removal = data.get('is_removal', False)
    current_balance = user_balances[user_id]['balance']

    # Получаем имя пользователя для удобства
    username = ""
    for ticket in tickets.values():
        if ticket['user_id'] == user_id:
            username = ticket['user_name']
            break

    user_info = f"👤 Пользователь: {username}" if username else f"👤 ID пользователя: {user_id}"

    if is_removal:
        message_text = (
            f"⚠️ *Списание баланса*\n\n"
            f"{user_info}\n"
            f"💳 Текущий баланс: {format_currency(current_balance)} ₽\n\n"
            f"💰 *Введите сумму для списания:*\n\n"
            f"Пример: 500 или 500.75\n"
            f"Максимум: {format_currency(current_balance)} ₽\n\n"
            f"Для отмены напишите /cancel"
        )
    else:
        message_text = (
            f"💰 *Начисление баланса*\n\n"
            f"{user_info}\n"
            f"💳 Текущий баланс: {format_currency(current_balance)} ₽\n\n"
            f"*Введите сумму для начисления:*\n\n"
            f"Пример: 500 или 500.75\n\n"
            f"Для отмены напишите /cancel"
        )

    await message.answer(message_text, parse_mode="Markdown")


# Добавьте обработчики для inline кнопок создания пользователя
@dp.callback_query(F.data.startswith('create_user_'))
async def create_new_user(callback_query: CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    user_id = int(callback_query.data.split('_')[2])

    # Создаем пользователя
    if user_id not in user_balances:
        user_balances[user_id] = {
            'balance': Decimal('0.00'),
            'transactions': [],
            'created_at': datetime.now()
        }

    if user_id not in referral_stats:
        referral_stats[user_id] = {
            'referrals': [],
            'count': 0,
            'earned': Decimal('0.00'),
            'created_at': datetime.now()
        }

    await state.update_data(user_id=user_id)
    await state.set_state(AdminBalanceStates.waiting_for_amount)

    data = await state.get_data()
    is_removal = data.get('is_removal', False)
    current_balance = user_balances[user_id]['balance']

    await callback_query.message.edit_text(
        f"✅ Пользователь с ID {user_id} создан!\n\n"
        f"Текущий баланс: {format_currency(current_balance)} ₽\n\n"
        f"Теперь введите сумму для {'списания' if is_removal else 'начисления'}:\n\n"
        f"Пример: 500 или 500.75\n"
        f"Для отмены напишите /cancel"
    )
    await callback_query.answer()


@dp.callback_query(F.data == "cancel_create_user")
async def cancel_create_user(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.message.edit_text(
        "❌ Операция отменена.\n\n"
        "Пользователь не был создан."
    )
    await callback_query.answer()


@dp.message(AdminBalanceStates.waiting_for_amount)
async def process_admin_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        await message.answer("❌ Недостаточно прав!")
        return

    if message.text == "/cancel" or message.text.lower() == "отмена":
        await state.clear()
        await message.answer(
            "❌ Операция отменена.",
            reply_markup=get_admin_panel_menu()
        )
        return

    try:
        amount = Decimal(message.text.replace(',', '.'))
        if amount <= 0:
            await message.answer("❌ Сумма должна быть положительным числом!\n\nПопробуйте еще раз:")
            return  # Не очищаем state, даем возможность исправить
    except:
        await message.answer("❌ Введите число! Пример: 500 или 500.75\n\nПопробуйте еще раз:")
        return  # Не очищаем state, даем возможность исправить

    data = await state.get_data()
    user_id = data.get('user_id')
    is_removal = data.get('is_removal', False)

    if is_removal:
        if user_id in user_balances:
            current_balance = user_balances[user_id]['balance']
            if amount > current_balance:
                await message.answer(
                    f"❌ *Недостаточно средств у пользователя!*\n\n"
                    f"💳 Текущий баланс: {format_currency(current_balance)} ₽\n"
                    f"💰 Попытка списать: {format_currency(amount)} ₽\n"
                    f"📉 Не хватает: {format_currency(amount - current_balance)} ₽\n\n"
                    f"Введите меньшую сумму или нажмите /cancel для отмены:"
                )
                return  # Не очищаем state, даем возможность исправить

    await state.update_data(amount=amount)
    await state.set_state(AdminBalanceStates.waiting_for_description)

    if is_removal:
        message_text = (
            f"📝 *Введите причину списания:*\n\n"
            f"Пример: 'Списание за отмененный заказ #{123}'\n"
            f"Или: 'Коррекция баланса'\n"
            f"Или: 'Возврат средств'\n\n"
            f"Для отмены напишите /cancel"
        )
    else:
        message_text = (
            "📝 *Введите описание операции:*\n\n"
            "Пример: 'Начисление за выполнение заказа #{123}'\n"
            "Или: 'Бонус за активность'\n"
            "Или: 'Исправление ошибки начисления'\n\n"
            f"Для отмены напишите /cancel"
        )

    await message.answer(message_text, parse_mode="Markdown")


@dp.message(AdminBalanceStates.waiting_for_description)
async def process_admin_description(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        await message.answer("❌ Недостаточно прав!")
        return

    if message.text == "/cancel" or message.text.lower() == "отмена":
        await state.clear()
        await message.answer(
            "❌ Операция отменена.",
            reply_markup=get_admin_panel_menu()
        )
        return

    # Проверяем, что описание не пустое
    if not message.text.strip():
        await message.answer("❌ Описание не может быть пустым!\n\nВведите описание операции:")
        return  # Не очищаем state, даем возможность исправить

    data = await state.get_data()
    user_id = data['user_id']
    amount = data['amount']
    is_removal = data.get('is_removal', False)
    description = message.text.strip()

    if user_id not in user_balances:
        user_balances[user_id] = {
            'balance': Decimal('0.00'),
            'transactions': [],
            'created_at': datetime.now()
        }

    if is_removal:
        actual_amount = -amount
        operation_type = 'admin_remove'
    else:
        actual_amount = amount
        operation_type = 'admin_add'

    # Двойная проверка баланса (на случай если что-то изменилось)
    if is_removal and user_balances[user_id]['balance'] < amount:
        await message.answer(
            f"❌ *Недостаточно средств у пользователя!*\n\n"
            f"💳 Текущий баланс: {format_currency(user_balances[user_id]['balance'])} ₽\n"
            f"💰 Попытка списать: {format_currency(amount)} ₽\n\n"
            f"Начните операцию заново или нажмите /cancel"
        )
        await state.clear()
        return

    # Выполняем операцию
    user_balances[user_id]['balance'] += actual_amount

    # Используем global transaction_id_counter
    global transaction_id_counter
    transaction_id_counter += 1

    user_balances[user_id]['transactions'].append({
        'id': transaction_id_counter,
        'type': operation_type,
        'amount': actual_amount,
        'status': 'completed',
        'created_at': datetime.now(),
        'description': f'{description} (Админ: {message.from_user.id})'
    })

    # Получаем имя пользователя для уведомления
    username = ""
    for ticket in tickets.values():
        if ticket['user_id'] == user_id:
            username = ticket['user_name']
            break

    try:
        if is_removal:
            notification_text = (
                f"⚠️ *Списание с баланса*\n\n"
                f"Администратор списал средства с вашего баланса.\n\n"
                f"📋 *Причина:* {description}\n"
                f"💰 *Сумма:* -{format_currency(amount)} ₽\n"
                f"💳 *Новый баланс:* {format_currency(user_balances[user_id]['balance'])} ₽\n\n"
                f"📅 *Дата:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
        else:
            notification_text = (
                f"💰 *Пополнение баланса*\n\n"
                f"Администратор пополнил ваш баланс.\n\n"
                f"📋 *Операция:* {description}\n"
                f"💰 *Сумма:* +{format_currency(amount)} ₽\n"
                f"💳 *Новый баланс:* {format_currency(user_balances[user_id]['balance'])} ₽\n\n"
                f"📅 *Дата:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )

        await bot.send_message(
            user_id,
            notification_text,
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

    # Формируем ответ администратору
    user_info = f"👤 Пользователь: {username}" if username else f"👤 ID пользователя: {user_id}"

    if is_removal:
        admin_message = f"✅ *Баланс списан!*"
        operation_text = f"Списано: -{format_currency(amount)} ₽"
    else:
        admin_message = f"✅ *Баланс пополнен!*"
        operation_text = f"Начислено: +{format_currency(amount)} ₽"

    result_message = (
        f"{admin_message}\n\n"
        f"{user_info}\n"
        f"💰 {operation_text}\n"
        f"💳 Новый баланс: {format_currency(user_balances[user_id]['balance'])} ₽\n"
        f"📝 Описание: {description}\n\n"
        f"📋 ID транзакции: #{transaction_id_counter}"
    )

    await message.answer(
        result_message,
        parse_mode="Markdown",
        reply_markup=get_admin_panel_menu()
    )

    await state.clear()


# ========== 🛒 УПРАВЛЕНИЕ КАТАЛОГОМ ==========

@dp.message(F.text == "🛒 Управление каталогом")
async def manage_catalog_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта функция доступна только администраторам.")
        return

    total_products = len(catalog)
    total_quantity = sum(p.get('quantity', 0) for p in catalog.values())

    catalog_text = (
        f"🛒 *Управление каталогом*\n\n"
        f"📊 *Статистика:*\n"
        f"• Товаров в каталоге: {total_products}\n"
        f"• Всего на складе: {total_quantity} шт.\n"
        f"• Средняя цена: {format_currency(sum(p['price'] for p in catalog.values()) / total_products) if total_products > 0 else Decimal('0.00')} ₽\n\n"
        f"Выберите действие:"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Добавить товар", callback_data="add_product")
    if total_products > 0:
        builder.button(text="✏️ Редактировать товар", callback_data="edit_product_list")
        builder.button(text="🗑️ Удалить товар", callback_data="delete_product_list")
        builder.button(text="📊 Статистика продаж", callback_data="sales_stats")
    builder.button(text="⬅️ Назад", callback_data="back_to_admin_panel")
    builder.adjust(2)

    await message.answer(catalog_text, parse_mode="Markdown", reply_markup=builder.as_markup())


@dp.callback_query(F.data == "add_product")
async def add_product_start(callback_query: CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    await state.set_state(AdminCatalogStates.waiting_for_product_name)
    await callback_query.message.answer(
        "📦 *Добавление нового товара*\n\n"
        "Введите название товара:",
        parse_mode="Markdown"
    )
    await callback_query.answer()


@dp.message(AdminCatalogStates.waiting_for_product_name)
async def process_admin_product_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    await state.update_data(product_name=message.text)
    await state.set_state(AdminCatalogStates.waiting_for_product_price)

    await message.answer(
        "💰 *Введите цену товара:*\n\n"
        "Пример: 1500 или 1500.50 (для 1500 рублей 50 копеек)",
        parse_mode="Markdown"
    )


@dp.message(AdminCatalogStates.waiting_for_product_price)
async def process_admin_product_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        price = Decimal(message.text.replace(',', '.'))
        if price <= 0:
            await message.answer("❌ Цена должна быть положительным числом!")
            return
    except:
        await message.answer("❌ Введите число! Пример: 1500.50")
        return

    await state.update_data(product_price=price)
    await state.set_state(AdminCatalogStates.waiting_for_product_quantity)

    await message.answer(
        f"📊 *Введите количество товара:*\n\n"
        f"💡 *Пример:* 100 (штук в наличии)\n"
        f"💡 Для цифровых товаров укажите 9999",
        parse_mode="Markdown"
    )


@dp.message(AdminCatalogStates.waiting_for_product_quantity)
async def process_admin_product_quantity(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        quantity = int(message.text)
        if quantity < 0:
            await message.answer("❌ Количество не может быть отрицательным!")
            return
    except ValueError:
        await message.answer("❌ Введите целое число!")
        return

    await state.update_data(product_quantity=quantity)
    await state.set_state(AdminCatalogStates.waiting_for_product_description)

    await message.answer(
        f"📝 *Введите описание товара:*\n\n"
        f"💡 *Пример:* 'Профессиональный дизайн логотипа для вашего бренда'",
        parse_mode="Markdown"
    )


@dp.message(AdminCatalogStates.waiting_for_product_description)
async def process_admin_product_description(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    await state.update_data(product_description=message.text)
    await state.set_state(AdminCatalogStates.waiting_for_referral_settings)

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, включить реферальную программу", callback_data="referral_enabled_yes")
    builder.button(text="❌ Нет, не включать", callback_data="referral_enabled_no")
    builder.adjust(1)

    await message.answer(
        f"👥 *Настройки реферальной программы для товара*\n\n"
        f"Хотите включить реферальную программу для этого товара?\n\n"
        f"💡 *Что это дает:*\n"
        f"• При покупке товара рефералом, его пригласитель получает бонус\n"
        f"• Бонус по умолчанию: {format_currency(bot_settings['referral_bonus'])} ₽\n"
        f"• Можно настроить индивидуальный бонус для товара\n\n"
        f"Выберите вариант:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data.in_(["referral_enabled_yes", "referral_enabled_no"]))
async def process_referral_settings(callback_query: CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    referral_enabled = callback_query.data == "referral_enabled_yes"
    await state.update_data(referral_enabled=referral_enabled)

    if referral_enabled:
        await state.set_state(AdminCatalogStates.waiting_for_referral_settings)

        await callback_query.message.answer(
            f"💰 *Введите сумму реферального бонуса для этого товара:*\n\n"
            f"Текущий стандартный бонус: {format_currency(bot_settings['referral_bonus'])} ₽\n\n"
            f"Пример: 50 или 75.25\n"
            f"Оставьте пустым, чтобы использовать стандартный бонус ({format_currency(bot_settings['referral_bonus'])} ₽)\n\n"
            f"💡 *Совет:* Для цифровых товаров можно установить бонус в процентах от цены",
            parse_mode="Markdown"
        )
    else:
        # Если реферальная программа не включена, переходим сразу к вводу username
        await state.update_data(referral_bonus=Decimal('0.00'))
        await state.set_state(AdminCatalogStates.waiting_for_product_username)
        
        await callback_query.message.answer(
            "👤 *Введите username для связи по товару:*\n\n"
            "Пример: @username или просто username\n"
            "Или напишите 'нет', если не требуется\n\n"
            "💡 Этот username будет отображаться при покупке товара администратору",
            parse_mode="Markdown"
        )

    await callback_query.answer()


@dp.message(AdminCatalogStates.waiting_for_referral_settings)
async def process_referral_bonus(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text.strip() == "":
        # Используем стандартный бонус
        referral_bonus = bot_settings['referral_bonus']
    else:
        try:
            referral_bonus = Decimal(message.text.replace(',', '.'))
            if referral_bonus < 0:
                await message.answer("❌ Бонус не может быть отрицательным! Введите положительное число.")
                return
        except:
            await message.answer("❌ Введите число! Пример: 50 или 75.25")
            return

    await state.update_data(referral_bonus=referral_bonus)

    # Переходим к запросу username
    await state.set_state(AdminCatalogStates.waiting_for_product_username)

    await message.answer(
        "👤 *Введите username для связи по товару:*\n\n"
        "Пример: @username или просто username\n"
        "Или напишите 'нет', если не требуется\n\n"
        "💡 Этот username будет отображаться при покупке товара администратору",
        parse_mode="Markdown"
    )


@dp.message(AdminCatalogStates.waiting_for_product_username)
async def process_admin_product_username(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        data = await state.get_data()
    except Exception as e:
        await message.answer(f"❌ Ошибка получения данных: {e}. Начните заново.")
        await state.clear()
        return

    # Проверяем, есть ли необходимые данные
    required_fields = ['product_name', 'product_price', 'product_description']
    for field in required_fields:
        if field not in data:
            await message.answer(f"❌ Ошибка: отсутствует {field}. Начните заново.")
            await state.clear()
            return

    # Генерируем новый ID для товара
    new_id = max(catalog.keys()) + 1 if catalog else 1

    # Создаем запись товара
    catalog[new_id] = {
        'name': data['product_name'],
        'price': Decimal(str(data['product_price'])).quantize(Decimal('0.01')),
        'quantity': data.get('product_quantity', 0),
        'description': data['product_description'],
        'referral_enabled': data.get('referral_enabled', False),
        'created_at': datetime.now(),
        'added_by': message.from_user.id
    }

    if data.get('referral_enabled', False):
        catalog[new_id]['referral_bonus'] = data.get('referral_bonus', bot_settings['referral_bonus'])

    # Добавляем username если указан
    if message.text.strip().lower() != 'нет' and message.text.strip():
        catalog[new_id]['contact_username'] = message.text.strip()

    referral_info = ""
    if catalog[new_id].get('referral_enabled', False):
        referral_bonus = catalog[new_id].get('referral_bonus', bot_settings['referral_bonus'])
        referral_info = f"\n👥 *Реферальная программа:* ✅ Включена\n💰 Реферальный бонус: {format_currency(referral_bonus)} ₽"

    contact_info = ""
    if 'contact_username' in catalog[new_id]:
        contact_info = f"\n📞 Контакт: {catalog[new_id]['contact_username']}"

    await message.answer(
        f"✅ *Товар успешно добавлен!*\n\n"
        f"🆔 ID: {new_id}\n"
        f"📦 Название: {data['product_name']}\n"
        f"💰 Цена: {format_currency(data['product_price'])} ₽\n"
        f"📊 Количество: {data.get('product_quantity', 0)} шт.\n"
        f"{referral_info}"
        f"{contact_info}\n"
        f"📝 Описание: {data['product_description'][:100]}...\n\n"
        f"💡 Товар доступен в каталоге!",
        parse_mode="Markdown",
        reply_markup=get_admin_panel_menu()
    )

    await state.clear()


@dp.callback_query(F.data == "edit_product_list")
async def edit_product_list(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    if not catalog:
        await callback_query.message.answer("📭 Каталог пуст.")
        return

    builder = InlineKeyboardBuilder()

    for product_id, product in catalog.items():
        status_icon = "👥" if product.get('referral_enabled', False) else "📦"
        builder.button(
            text=f"{status_icon} {product['name']} - {format_currency(product['price'])} ₽ ({product.get('quantity', 0)} шт.)",
            callback_data=f"edit_product_{product_id}"
        )

    builder.button(text="⬅️ Назад", callback_data="back_to_admin_panel")
    builder.adjust(1)

    await callback_query.message.edit_text(
        "✏️ *Выберите товар для редактирования:*\n\n"
        "👥 - с реферальной программой\n"
        "📦 - без реферальной программы",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@dp.callback_query(F.data == "delete_product_list")
async def delete_product_list(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    if not catalog:
        await callback_query.message.answer("📭 Каталог пуст.")
        return

    builder = InlineKeyboardBuilder()

    for product_id, product in catalog.items():
        status_icon = "👥" if product.get('referral_enabled', False) else "📦"
        builder.button(
            text=f"{status_icon} {product['name']} - {format_currency(product['price'])} ₽ ({product.get('quantity', 0)} шт.)",
            callback_data=f"delete_product_{product_id}"
        )

    builder.button(text="⬅️ Назад", callback_data="back_to_admin_panel")
    builder.adjust(1)

    await callback_query.message.edit_text(
        "🗑️ *Выберите товар для удаления:*\n\n"
        "👥 - с реферальной программой\n"
        "📦 - без реферальной программы",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@dp.callback_query(F.data.startswith('delete_product_'))
async def delete_product_confirm(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    product_id = int(callback_query.data.split('_')[2])
    product = catalog.get(product_id)

    if not product:
        await callback_query.answer("❌ Товар не найден!")
        return

    has_orders = False
    for orders in user_orders.values():
        for order in orders:
            if order['product_id'] == product_id and order['status'] in ['processing', 'pending']:
                has_orders = True
                break
        if has_orders:
            break

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=f"confirm_delete_{product_id}")
    builder.button(text="❌ Нет, отмена", callback_data="delete_product_list")
    builder.adjust(2)

    warning_text = ""
    if has_orders:
        warning_text = "\n\n⚠️ *Внимание!* На этот товар есть активные заказы!"

    referral_info = ""
    if product.get('referral_enabled', False):
        referral_bonus = product.get('referral_bonus', bot_settings['referral_bonus'])
        referral_info = f"\n👥 Реферальная программа: ✅ Включена\n💰 Реферальный бонус: {format_currency(referral_bonus)} ₽"

    await callback_query.message.edit_text(
        f"🗑️ *Подтверждение удаления*\n\n"
        f"Вы уверены, что хотите удалить товар?\n\n"
        f"📦 *{product['name']}*\n"
        f"💰 Цена: {format_currency(product['price'])} ₽\n"
        f"📊 В наличии: {product.get('quantity', 0)} шт.\n"
        f"{referral_info}"
        f"{warning_text}\n\n"
        f"⚠️ *Это действие нельзя отменить!*",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@dp.callback_query(F.data.startswith('confirm_delete_'))
async def confirm_delete_product(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    product_id = int(callback_query.data.split('_')[2])

    if product_id in catalog:
        product_name = catalog[product_id]['name']
        del catalog[product_id]

        await callback_query.message.edit_text(
            f"✅ Товар '{product_name}' успешно удален!",
            reply_markup=InlineKeyboardBuilder()
            .button(text="🛒 Управление каталогом", callback_data="admin_catalog")
            .as_markup()
        )
    else:
        await callback_query.answer("❌ Товар не найден!")

    await callback_query.answer()


@dp.callback_query(F.data == "sales_stats")
async def sales_stats(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    product_stats = {}
    total_revenue = Decimal('0.00')
    total_orders_count = 0
    total_items_sold = 0

    # Статистика по реферальным товарам
    referral_products_stats = {}
    total_referral_revenue = Decimal('0.00')
    total_referral_items = 0

    for user_id, orders in user_orders.items():
        for order in orders:
            product_id = order['product_id']
            if product_id not in product_stats:
                product_stats[product_id] = {
                    'name': catalog.get(product_id, {}).get('name', f'Товар #{product_id}'),
                    'count': 0,
                    'revenue': Decimal('0.00'),
                    'referral_enabled': catalog.get(product_id, {}).get('referral_enabled', False)
                }

            product_stats[product_id]['count'] += order['quantity']
            product_stats[product_id]['revenue'] += order['total_price']
            total_revenue += order['total_price']
            total_orders_count += 1
            total_items_sold += order['quantity']

            # Статистика по реферальным товарам
            if product_stats[product_id]['referral_enabled']:
                if product_id not in referral_products_stats:
                    referral_products_stats[product_id] = {
                        'name': product_stats[product_id]['name'],
                        'count': 0,
                        'revenue': Decimal('0.00')
                    }
                referral_products_stats[product_id]['count'] += order['quantity']
                referral_products_stats[product_id]['revenue'] += order['total_price']
                total_referral_revenue += order['total_price']
                total_referral_items += order['quantity']

    if not product_stats:
        await callback_query.message.answer("📊 *Статистика продаж*\n\nПока нет продаж.")
        return

    stats_text = f"📊 *СТАТИСТИКА ПРОДАЖ*\n\n"
    stats_text += f"📈 *Общее:*\n"
    stats_text += f"• Всего заказов: {total_orders_count}\n"
    stats_text += f"• Продано товаров: {total_items_sold} шт.\n"
    stats_text += f"• Общая выручка: {format_currency(total_revenue)} ₽\n"
    stats_text += f"• Средний чек: {format_currency(total_revenue / total_orders_count) if total_orders_count > 0 else Decimal('0.00')} ₽\n\n"

    if referral_products_stats:
        stats_text += f"👥 *Товары с реферальной программой:*\n"
        stats_text += f"• Продано товаров: {total_referral_items} шт.\n"
        stats_text += f"• Выручка: {format_currency(total_referral_revenue)} ₽\n"
        stats_text += f"• Доля от общей выручки: {format_currency(total_referral_revenue / total_revenue * 100) if total_revenue > 0 else Decimal('0.00')}%\n\n"

    stats_text += f"🏆 *Топ товаров:*\n"

    sorted_stats = sorted(product_stats.items(), key=lambda x: x[1]['revenue'], reverse=True)

    for product_id, stats in sorted_stats[:10]:
        current_quantity = catalog.get(product_id, {}).get('quantity', 0)
        referral_icon = "👥" if stats['referral_enabled'] else "📦"
        stats_text += f"{referral_icon} {stats['name']}\n"
        stats_text += f"   📊 Продано: {stats['count']} шт.\n"
        stats_text += f"   💰 Выручка: {format_currency(stats['revenue'])} ₽\n"
        stats_text += f"   📦 Осталось: {current_quantity} шт.\n\n"

    await callback_query.message.answer(stats_text, parse_mode="Markdown")
    await callback_query.answer()


@dp.callback_query(F.data == "admin_catalog")
async def admin_catalog_callback(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    await manage_catalog_admin(callback_query.message)
    await callback_query.answer()


# ========== ПЕРЕКЛЮЧЕНИЕ МЕЖДУ МЕНЮ ==========

@dp.message(F.text == "👤 Выйти в меню пользователя")
async def switch_to_user_menu(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта функция доступна только администраторам.")
        return

    await message.answer(
        "✅ Переключено на пользовательскую клавиатуру!\n\n"
        "Для возврата в админ-панель нажмите кнопку '👨‍💼 Админ-панель'",
        reply_markup=get_admin_main_menu()
    )


# Добавьте этот обработчик если его нет
@dp.message(F.text == "👨‍💼 Админ-панель")
async def return_to_admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав администратора!")
        return

    await message.answer(
        "👨‍💼 *Панель администратора*\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=get_admin_panel_menu()
    )


@dp.message(F.text == "🏠 Главное меню")
async def return_to_main_menu(message: Message):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if is_admin_user:
        await message.answer(
            "🏠 *Главное меню*\n\n"
            "Выберите действие:",
            parse_mode="Markdown",
            reply_markup=get_admin_main_menu()
        )
    else:
        await message.answer(
            "🏠 *Главное меню*\n\n"
            "Выберите действие:",
            parse_mode="Markdown",
            reply_markup=get_main_menu()
        )


@dp.callback_query(F.data == "back_to_main_menu")
async def back_to_main_menu_callback(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if is_admin_user:
        await callback_query.message.edit_text(
            "🏠 *Главное меню*\n\n"
            "Выберите действие:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardBuilder()
            .button(text="🛒 Каталог", callback_data="back_to_catalog_main")
            .button(text="📦 Мои заказы", callback_data="view_orders_user")
            .button(text="💰 Баланс", callback_data="balance_info")
            .button(text="🛟 Поддержка", callback_data="support_info")
            .button(text="👥 Рефералы", callback_data="referral_info")
            .button(text="👨‍💼 Админ-панель", callback_data="admin_panel")
            .adjust(2)
            .as_markup()
        )
    else:
        await callback_query.message.edit_text(
            "🏠 *Главное меню*\n\n"
            "Выберите действие:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardBuilder()
            .button(text="🛒 Каталог", callback_data="back_to_catalog_main")
            .button(text="📦 Мои заказы", callback_data="view_orders_user")
            .button(text="💰 Баланс", callback_data="balance_info")
            .button(text="🛟 Поддержка", callback_data="support_info")
            .button(text="👥 Рефералы", callback_data="referral_info")
            .adjust(2)
            .as_markup()
        )
    await callback_query.answer()


# ========== КОМАНДЫ ==========

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    if len(message.text.split()) > 1:
        referrer_code = message.text.split()[1]
        await process_new_user(user_id, referrer_code)

    if user_id not in referral_stats:
        referral_stats[user_id] = {
            'referrals': [],
            'count': 0,
            'earned': Decimal('0.00'),
            'created_at': datetime.now()
        }

    if is_admin_user:
        await message.answer(
            "👨‍💼 *Добро пожаловать в панель администратора!*\n\n"
            "Выберите действие:",
            parse_mode="Markdown",
            reply_markup=get_admin_main_menu()
        )
    else:
        if user_id not in referral_links:
            code = generate_referral_link(user_id)
            welcome_text = (
                f"👋 *Добро пожаловать в наш сервис!*\n\n"

                f"🎁 *Вам доступны:*\n"
                f"• Приветственный бонус: *{format_currency(bot_settings['welcome_bonus'])}* ₽\n"
                f"• Широкий каталог услуг 🛒\n"
                f"• Реферальная программа 👥\n"
                f"• Быстрая поддержка 🛟\n\n"

                f"💎 *Ваш реферальный код:* `{code}`\n"
                f"Приглашайте друзей и получайте бонусы!\n\n"

                f"✨ *Начните с:*\n"
                f"1. Пополнения баланса 💰\n"
                f"2. Просмотра каталога 🛒\n"
                f"3. Приглашения друзей 👥\n\n"

                f"💫 Желаем приятного использования!"
            )
            await message.answer(
                welcome_text,
                parse_mode="Markdown",
                reply_markup=get_main_menu()
            )
        else:
            await message.answer(
                "👋 *С возвращением!*\n\n"
                "Выберите действие:",
                parse_mode="Markdown",
                reply_markup=get_main_menu()
            )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    help_text = (
        "🆘 *Помощь по командам*\n\n"

        "📋 *Основные команды:*\n"
        "• /start - Начать работу с ботом\n"
        "• /help - Показать эту справку\n"
        "• /balance - Показать баланс\n"
        "• /catalog - Открыть каталог\n"
        "• /orders - Мои заказы\n"
        "• /support - Служба поддержки\n"
        "• /referral - Реферальная система\n\n"

        "👨‍💼 *Админ команды:*\n"
        "• /admin - Панель администратора\n"
        "• /stats - Статистика бота\n"
        "• /broadcast - Сделать рассылку\n\n"

        "💡 *Совет:* Используйте кнопки меню для быстрого доступа к функциям!"
    )

    await message.answer(help_text, parse_mode="Markdown")


@dp.message(Command("admin"))
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав администратора!")
        return

    await message.answer(
        "👨‍💼 *Панель администратора*\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=get_admin_panel_menu()
    )


@dp.message(Command("balance"))
async def balance_command(message: Message):
    await balance_menu(message)


@dp.message(Command("catalog"))
async def catalog_command(message: Message):
    await show_catalog(message)


@dp.message(Command("orders"))
async def orders_command(message: Message):
    await show_orders(message)


@dp.message(Command("support"))
async def support_command(message: Message):
    await support_menu(message)


@dp.message(Command("referral"))
async def referral_command(message: Message):
    await show_referral_user_menu(message)


# ========== CALLBACK ОБРАБОТЧИКИ ДЛЯ ИНЛАЙН КНОПОК ==========

@dp.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    await admin_statistics(callback_query.message)
    await callback_query.answer()


@dp.callback_query(F.data == "admin_tickets")
async def admin_tickets_callback(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    await admin_open_tickets(callback_query.message)
    await callback_query.answer()


@dp.callback_query(F.data == "admin_balances")
async def admin_balances_callback(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    await manage_balances_menu(callback_query.message)
    await callback_query.answer()


@dp.callback_query(F.data == "admin_referrals")
async def admin_referrals_callback(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    await admin_referral_menu(callback_query.message)
    await callback_query.answer()


@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_callback(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    await broadcast_menu_admin(callback_query.message)
    await callback_query.answer()


@dp.callback_query(F.data == "admin_settings")
async def admin_settings_callback(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    await settings_menu(callback_query.message)
    await callback_query.answer()


@dp.callback_query(F.data == "balance_info")
async def balance_info_callback(callback_query: CallbackQuery):
    await balance_menu(callback_query.message)
    await callback_query.answer()


@dp.callback_query(F.data == "support_info")
async def support_info_callback(callback_query: CallbackQuery):
    await support_menu(callback_query.message)
    await callback_query.answer()


@dp.callback_query(F.data == "referral_info")
async def referral_info_callback(callback_query: CallbackQuery):
    await show_referral_user_menu(callback_query.message)
    await callback_query.answer()


@dp.callback_query(F.data == "admin_panel")
async def admin_panel_callback(callback_query: CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Недостаточно прав!")
        return

    await return_to_admin_panel(callback_query.message)
    await callback_query.answer()


# ========== УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК ==========

@dp.message()
async def universal_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if message.text == "/cancel":
        current_state = await state.get_state()
        if current_state:
            await state.clear()
            await message.answer(
                "❌ Действие отменено.",
                reply_markup=get_main_menu() if not is_admin_user else get_admin_main_menu()
            )
        return

    if bot_settings['maintenance'] and not is_admin_user:
        current_state = await state.get_state()
        if current_state:
            return

        if message.text.startswith('/'):
            return

        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    # Если сообщение не обработано другими хэндлерами
    if message.text not in ["🛒 Каталог", "📦 Мои заказы", "💰 Баланс", "🛟 Поддержка", "👥 Рефералы",
                            "👨‍💼 Админ-панель", "🏠 Главное меню", "📊 Статистика", "🎫 Поддержка (Админ)",
                            "💰 Управление балансами", "👥 Рефералы (Админ)", "📢 Рассылка", "⚙️ Настройки",
                            "🛒 Управление каталогом", "👤 Выйти в меню пользователя", "📋 Открытые тикеты",
                            "📊 Статистика тикетов", "➕ Начислить баланс", "➖ Списать баланс", "📋 Список пользователей",
                            "⬅️ Назад в админку", "🎫 Создать тикет", "📋 Мои тикеты"]:
        await message.answer(
            "ℹ️ Используйте кнопки меню для навигации",
            reply_markup=get_main_menu() if not is_admin_user else get_admin_main_menu()
        )


# ========== ЗАПУСК БОТА ==========

async def main():
    print("🤖 Бот магазина с поддержкой и реферальной системой запущен...")
    print("📡 Статус: Ожидание сообщений...")
    print(f"👥 Пользователей в базе: {len(referral_stats)}")
    print(f"🛒 Товаров в каталоге: {len(catalog)}")
    print(f"💰 Всего балансов: {len(user_balances)}")
    print(f"🎫 Активных тикетов: {sum(1 for t in tickets.values() if t['status'] == 'open')}")

    # Статистика по реферальным товарам
    referral_products_count = sum(1 for p in catalog.values() if p.get('referral_enabled', False))
    print(f"👥 Товаров с реферальной программой: {referral_products_count}")

    if bot_settings['maintenance']:
        print("🔧 Режим техобслуживания: ВКЛЮЧЕН")
    else:
        print("✅ Режим техобслуживания: ВЫКЛЮЧЕН")

    await dp.start_polling(bot, skip_updates=True)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")