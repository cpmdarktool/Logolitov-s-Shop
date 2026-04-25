import logging
import asyncio
import hashlib
import requests
import json
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
ADMIN_ID = [6691790154, 6081656814, 5769248725]  # ID администратора (добавлен новый)
BOT_USERNAME = 'logolinov_tg_bot'  # Без @

# CryptoBot API настройки
CRYPTO_BOT_TOKEN = '573098:AAx9n0XEj0mIxM5TEcyIHV5k6OX6KABMe9N'
CRYPTO_BOT_API_URL = 'https://pay.crypt.bot/api/'

# Настройки пополнения
MIN_DEPOSIT_USDT = Decimal('1')   # Минимальное пополнение 1 USDT (изменено)
MAX_DEPOSIT_USDT = Decimal('50000')  # Максимальное пополнение 50000 USDT

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

# База данных для платежей через CryptoBot
pending_payments = {}  # invoice_id -> {'user_id': int, 'amount': Decimal, 'transaction_id': int, 'status': str, 'created_at': datetime}

# Настройки бота
bot_settings = {
    'maintenance': False,
    'maintenance_message': 'Бот на техническом обслуживании',
    'welcome_bonus': Decimal('100.00'),  # Бонус за регистрацию (в рублях)
    'referral_bonus': Decimal('50.00'),  # Бонус за реферала (в рублях)
    'usdt_to_rub_rate': Decimal('100'),  # Курс USDT к RUB (1 USDT = 100 RUB)
}


# ========== ФУНКЦИИ CRYPTOBOT ==========

def create_crypto_invoice(amount_usdt: Decimal, description: str = 'Пополнение баланса') -> dict:
    """Создание счета через CryptoBot API в USDT"""
    try:
        payload = {
            'asset': 'USDT',
            'amount': str(float(amount_usdt)),
            'description': description,
            'paid_btn_name': 'callback',
            'paid_btn_url': 'https://t.me/' + BOT_USERNAME
        }
        
        headers = {
            'Crypto-Pay-API-Token': CRYPTO_BOT_TOKEN,
            'Content-Type': 'application/json'
        }
        
        response = requests.post(
            CRYPTO_BOT_API_URL + 'createInvoice',
            headers=headers,
            json=payload
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                return {
                    'success': True,
                    'invoice_id': data['result']['invoice_id'],
                    'pay_url': data['result']['pay_url'],
                    'status': data['result']['status']
                }
        return {'success': False, 'error': 'Ошибка создания счета'}
    except Exception as e:
        logging.error(f"CryptoBot create invoice error: {e}")
        return {'success': False, 'error': str(e)}


def check_invoice_status(invoice_id: int) -> dict:
    """Проверка статуса счета через CryptoBot API"""
    try:
        payload = {
            'invoice_ids': invoice_id
        }
        
        headers = {
            'Crypto-Pay-API-Token': CRYPTO_BOT_TOKEN,
            'Content-Type': 'application/json'
        }
        
        response = requests.post(
            CRYPTO_BOT_API_URL + 'getInvoices',
            headers=headers,
            json=payload
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok') and data['result']['items']:
                invoice = data['result']['items'][0]
                return {
                    'success': True,
                    'status': invoice['status'],
                    'paid_amount': Decimal(str(invoice.get('paid_amount', 0))),
                    'paid_asset': invoice.get('paid_asset', 'USDT')
                }
        return {'success': False, 'status': 'unknown'}
    except Exception as e:
        logging.error(f"CryptoBot check invoice error: {e}")
        return {'success': False, 'status': 'unknown'}


async def process_crypto_payment(user_id: int, amount_usdt: Decimal, transaction_id: int):
    """Создание крипто-счета и отправка пользователю"""
    # Проверка минимальной и максимальной суммы
    if amount_usdt < MIN_DEPOSIT_USDT:
        return False, f'Минимальная сумма пополнения: {MIN_DEPOSIT_USDT} USDT'
    if amount_usdt > MAX_DEPOSIT_USDT:
        return False, f'Максимальная сумма пополнения: {MAX_DEPOSIT_USDT} USDT'
    
    invoice = create_crypto_invoice(amount_usdt)
    
    if not invoice['success']:
        return False, invoice.get('error', 'Ошибка создания платежа')
    
    # Сохраняем информацию о платеже
    pending_payments[invoice['invoice_id']] = {
        'user_id': user_id,
        'amount_usdt': amount_usdt,
        'transaction_id': transaction_id,
        'status': 'pending',
        'created_at': datetime.now()
    }
    
    return True, {
        'invoice_id': invoice['invoice_id'],
        'pay_url': invoice['pay_url'],
        'amount_usdt': amount_usdt
    }


def usdt_to_rub(usdt_amount: Decimal) -> Decimal:
    """Конвертация USDT в RUB по текущему курсу"""
    return (usdt_amount * bot_settings['usdt_to_rub_rate']).quantize(Decimal('0.01'))


def rub_to_usdt(rub_amount: Decimal) -> Decimal:
    """Конвертация RUB в USDT по текущему курсу"""
    return (rub_amount / bot_settings['usdt_to_rub_rate']).quantize(Decimal('0.01'))


async def check_and_confirm_payments():
    """Фоновая проверка статусов платежей"""
    while True:
        try:
            for invoice_id, payment in list(pending_payments.items()):
                if payment['status'] != 'pending':
                    continue
                    
                # Проверяем платежи старше 60 минут
                time_diff = (datetime.now() - payment['created_at']).total_seconds()
                if time_diff > 3600:  # 60 минут
                    payment['status'] = 'expired'
                    # Уведомляем пользователя об истечении
                    try:
                        await bot.send_message(
                            payment['user_id'],
                            f"⏰ *Срок оплаты истек!*\n\n"
                            f"Платеж на сумму {payment['amount_usdt']} USDT не был оплачен в течение часа.\n"
                            f"Для пополнения создайте новый платеж.",
                            parse_mode="Markdown"
                        )
                    except:
                        pass
                    continue
                
                # Проверяем статус
                status_check = check_invoice_status(invoice_id)
                
                if status_check.get('status') == 'paid':
                    payment['status'] = 'paid'
                    
                    # Начисляем средства пользователю
                    user_id = payment['user_id']
                    amount_usdt = payment['amount_usdt']
                    amount_rub = usdt_to_rub(amount_usdt)
                    transaction_id = payment['transaction_id']
                    
                    if user_id not in user_balances:
                        user_balances[user_id] = {
                            'balance': Decimal('0.00'),
                            'transactions': [],
                            'created_at': datetime.now()
                        }
                    
                    # Обновляем транзакцию
                    for transaction in user_balances[user_id]['transactions']:
                        if transaction['id'] == transaction_id:
                            transaction['status'] = 'completed'
                            transaction['paid_amount_usdt'] = amount_usdt
                            transaction['paid_amount_rub'] = amount_rub
                            break
                    
                    # Начисляем баланс в рублях
                    user_balances[user_id]['balance'] += amount_rub
                    
                    # Уведомляем пользователя
                    try:
                        await bot.send_message(
                            user_id,
                            f"✅ *Оплата подтверждена!*\n\n"
                            f"💰 Поступило: *{amount_usdt} USDT* (~{amount_rub} ₽)\n"
                            f"💳 Новый баланс: *{format_currency(user_balances[user_id]['balance'])}* ₽\n"
                            f"📋 ID транзакции: #{transaction_id}\n\n"
                            f"💡 Курс: 1 USDT = {bot_settings['usdt_to_rub_rate']} ₽",
                            parse_mode="Markdown"
                        )
                    except:
                        pass
                    
                    # Уведомление админам
                    for admin_id in ADMIN_ID:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"💰 *НОВОЕ ПОПОЛНЕНИЕ (USDT)*\n\n"
                                f"👤 Пользователь: ID `{user_id}`\n"
                                f"💳 Сумма: {amount_usdt} USDT (~{amount_rub} ₽)\n"
                                f"📋 ID транзакции: #{transaction_id}\n"
                                f"🔗 Invoice ID: {invoice_id}",
                                parse_mode="Markdown"
                            )
                        except:
                            pass
                    
                    logging.info(f"Payment confirmed: user {user_id}, {amount_usdt} USDT, invoice {invoice_id}")
            
            await asyncio.sleep(10)  # Проверяем каждые 10 секунд
        except Exception as e:
            logging.error(f"Payment check error: {e}")
            await asyncio.sleep(30)


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


# ========== 💰 БАЛАНС И ПОПОЛНЕНИЕ (CRYPTOBOT USDT) ==========

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
        f"💡 *Курс обмена:* 1 USDT = {bot_settings['usdt_to_rub_rate']} ₽\n\n"
        f"📈 *Доступные действия:*",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )


@dp.message(F.text == "💰 Пополнить баланс")
async def deposit_balance(message: Message):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await message.answer(f"⚠️ {bot_settings['maintenance_message']}")
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="1 USDT", callback_data="deposit_1")
    builder.button(text="3 USDT", callback_data="deposit_3")
    builder.button(text="5 USDT", callback_data="deposit_5")
    builder.button(text="10 USDT", callback_data="deposit_10")
    builder.button(text="20 USDT", callback_data="deposit_20")
    builder.button(text="50 USDT", callback_data="deposit_50")
    builder.button(text="100 USDT", callback_data="deposit_100")
    builder.button(text="Другая сумма", callback_data="deposit_custom")
    builder.adjust(2)

    await message.answer(
        f"💰 *Пополнение баланса в USDT*\n\n"
        f"💡 *Минимальная сумма:* {MIN_DEPOSIT_USDT} USDT\n"
        f"💡 *Максимальная сумма:* {MAX_DEPOSIT_USDT} USDT\n"
        f"💱 *Курс:* 1 USDT = {bot_settings['usdt_to_rub_rate']} ₽\n\n"
        f"Выберите сумму пополнения в USDT:",
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
            f"💰 *Введите сумму пополнения в USDT:*\n\n"
            f"Пример: 5.5 или 10 или 25.75\n"
            f"Минимум: {MIN_DEPOSIT_USDT} USDT\n"
            f"Максимум: {MAX_DEPOSIT_USDT} USDT\n\n"
            f"💱 Курс: 1 USDT = {bot_settings['usdt_to_rub_rate']} ₽",
            parse_mode="Markdown"
        )
        await callback_query.answer()
        return

    amount_str = callback_query.data.split('_')[1]
    
    try:
        amount_usdt = Decimal(amount_str)
    except:
        await callback_query.answer("❌ Ошибка в сумме!", show_alert=True)
        return

    if amount_usdt < MIN_DEPOSIT_USDT:
        await callback_query.answer(f"❌ Минимальная сумма: {MIN_DEPOSIT_USDT} USDT", show_alert=True)
        return
    if amount_usdt > MAX_DEPOSIT_USDT:
        await callback_query.answer(f"❌ Максимальная сумма: {MAX_DEPOSIT_USDT} USDT", show_alert=True)
        return

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
        'amount_usdt': amount_usdt,
        'amount_rub': usdt_to_rub(amount_usdt),
        'status': 'pending',
        'created_at': datetime.now(),
        'description': f'Пополнение на {amount_usdt} USDT'
    }

    user_balances[user_id]['transactions'].append(transaction)

    # Создаем платеж через CryptoBot
    success, result = await process_crypto_payment(user_id, amount_usdt, transaction_id_counter)

    if not success:
        await callback_query.message.answer(
            f"❌ *Ошибка создания платежа*\n\n{result}\n\nПопробуйте позже или обратитесь в поддержку.",
            parse_mode="Markdown"
        )
        await callback_query.answer()
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Перейти к оплате USDT", url=result['pay_url'])
    builder.button(text="🔄 Проверить оплату", callback_data=f"check_payment_{result['invoice_id']}")
    builder.adjust(1)

    amount_rub = usdt_to_rub(amount_usdt)

    await callback_query.message.answer(
        f"💳 *Оплата {amount_usdt} USDT*\n\n"
        f"📋 *Детали платежа:*\n"
        f"• Сумма: *{amount_usdt} USDT* (~{amount_rub} ₽)\n"
        f"• Минимальная сумма: {MIN_DEPOSIT_USDT} USDT\n"
        f"• ID транзакции: #{transaction_id_counter}\n"
        f"• Статус: Ожидает оплаты\n\n"
        f"🔗 *Для оплаты нажмите на кнопку ниже:*\n\n"
        f"💡 *Инструкция:*\n"
        f"1. Нажмите 'Перейти к оплате USDT'\n"
        f"2. Оплатите счет через CryptoBot\n"
        f"3. После оплаты нажмите 'Проверить оплату'\n\n"
        f"⏰ *Счет действителен 60 минут*",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@dp.callback_query(F.data.startswith('check_payment_'))
async def check_payment_status(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin_user = is_admin(user_id)

    if bot_settings['maintenance'] and not is_admin_user:
        await callback_query.answer(f"⚠️ {bot_settings['maintenance_message']}", show_alert=True)
        return

    invoice_id = int(callback_query.data.split('_')[2])

    payment = pending_payments.get(invoice_id)
    if not payment:
        await callback_query.answer("❌ Платеж не найден!", show_alert=True)
        return

    if payment['status'] == 'paid':
        await callback_query.answer("✅ Платеж уже оплачен!", show_alert=True)
        return
    elif payment['status'] == 'expired':
        await callback_query.answer("⏰ Срок оплаты истек! Создайте новый платеж.", show_alert=True)
        return

    # Проверяем статус
    status_check = check_invoice_status(invoice_id)

    if status_check.get('status') == 'paid':
        payment['status'] = 'paid'
        
        # Начисляем средства
        amount_usdt = payment['amount_usdt']
        amount_rub = usdt_to_rub(amount_usdt)
        transaction_id = payment['transaction_id']
        
        if user_id not in user_balances:
            user_balances[user_id] = {
                'balance': Decimal('0.00'),
                'transactions': [],
                'created_at': datetime.now()
            }
        
        # Обновляем транзакцию
        for transaction in user_balances[user_id]['transactions']:
            if transaction['id'] == transaction_id:
                transaction['status'] = 'completed'
                transaction['paid_amount_usdt'] = amount_usdt
                transaction['paid_amount_rub'] = amount_rub
                break
        
        user_balances[user_id]['balance'] += amount_rub
        
        await callback_query.message.edit_text(
            f"✅ *Оплата подтверждена!*\n\n"
            f"💰 Поступило: *{amount_usdt} USDT* (~{amount_rub} ₽)\n"
            f"💳 Новый баланс: *{format_currency(user_balances[user_id]['balance'])}* ₽\n"
            f"📋 ID транзакции: #{transaction_id}\n\n"
            f"💡 Курс: 1 USDT = {bot_settings['usdt_to_rub_rate']} ₽",
            parse_mode="Markdown"
        )
        
        # Уведомление админам
        for admin_id in ADMIN_ID:
            try:
                await bot.send_message(
                    admin_id,
                    f"💰 *НОВОЕ ПОПОЛНЕНИЕ (USDT)*\n\n"
                    f"👤 Пользователь: ID `{user_id}`\n"
                    f"💳 Сумма: {amount_usdt} USDT (~{amount_rub} ₽)\n"
                    f"📋 ID транзакции: #{transaction_id}",
                    parse_mode="Markdown"
                )
            except:
                pass
        
        await callback_query.answer("✅ Баланс пополнен!")
    else:
        await callback_query.answer("⏳ Платежまだ не оплачен. Попробуйте еще раз через минуту.", show_alert=True)


# ========== ОСТАЛЬНЫЕ ФУНКЦИИ (РЕФЕРАЛЫ, ПОДДЕРЖКА, КАТАЛОГ И Т.Д.) ==========
# [ВСЕ ОСТАЛЬНЫЕ ХЭНДЛЕРЫ ОСТАЮТСЯ БЕЗ ИЗМЕНЕНИЙ]
# Обратите внимание: в этом файле сохранены только основные изменения.
# Для полной функциональности нужно добавить все остальные хэндлеры из оригинального файла.


# ========== ЗАПУСК БОТА ==========

async def main():
    print("🤖 Бот магазина с поддержкой и реферальной системой запущен...")
    print(f"👑 Администраторы: {ADMIN_ID}")
    print(f"💰 Минимальное пополнение: {MIN_DEPOSIT_USDT} USDT")
    print(f"💱 Курс: 1 USDT = {bot_settings['usdt_to_rub_rate']} ₽")
    print("📡 Статус: Ожидание сообщений...")
    
    # Запускаем фоновую проверку платежей
    asyncio.create_task(check_and_confirm_payments())
    
    await dp.start_polling(bot, skip_updates=True)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
