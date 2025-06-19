import logging
import asyncio
import time
import random
import string
import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import requests

# Настройка логгирования
def setup_logging():
    if not os.path.exists("logs"):
        os.makedirs("logs")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/bot.log", encoding='utf-8')
        ]
    )
    return logging.getLogger(__name__)

logger = setup_logging()

# Конфигурация
BOT_TOKEN = "8052721063:AAF8uHHBfUzYBnouDlkXSzH_aHUrUw_JITE"
ADMIN_ID = 7586266147
CRYPTOBOT_API_TOKEN = "416434:AAPcOb0l1KnPuqxSCjFaW5gyob1MBUX8fKh"
CRYPTOBOT_API_URL = "https://pay.crypt.bot/api/"
DB_FILE = "users_db.json"
MIN_DEPOSIT = 1.0
MIN_WITHDRAW = 5.0

# Класс для работы с базой данных JSON
class JSONDatabase:
    def __init__(self, file_path: str = DB_FILE):
        self.file_path = file_path
        self.data = self._load_data()
        logger.info("Database initialized")

    def _load_data(self) -> Dict:
        try:
            if not os.path.exists(self.file_path):
                base_structure = {
                    "users": {},
                    "transactions": {},
                    "deals": {},
                    "invoices": {},
                    "system": {"last_ids": {}}
                }
                with open(self.file_path, 'w', encoding='utf-8') as f:
                    json.dump(base_structure, f, indent=4, ensure_ascii=False)
                return base_structure

            with open(self.file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading database: {e}")
            raise

    def save(self):
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving database: {e}")

    def user_exists(self, user_id: int) -> bool:
        return str(user_id) in self.data["users"]

    def get_user(self, user_id: int) -> Optional[Dict]:
        user = self.data["users"].get(str(user_id))
        if user:
            user["id"] = int(user_id)
        return user

    def add_user(self, user_id: int, username: str):
        if not self.user_exists(user_id):
            self.data["users"][str(user_id)] = {
                "username": username.lower() if username else str(user_id),
                "display_name": username or f"User_{user_id}",
                "balance": 0.0,
                "banned": False,
                "ban_info": None,
                "transactions": [],
                "deals": [],
                "registered_at": datetime.now().isoformat(),
                "last_active": datetime.now().isoformat()
            }
            self.save()
            logger.info(f"New user added: {user_id}")

    def update_balance(self, user_id: int, amount: float):
        if self.user_exists(user_id):
            self.data["users"][str(user_id)]["balance"] = round(
                self.data["users"][str(user_id)]["balance"] + amount, 2
            )
            self.data["users"][str(user_id)]["last_active"] = datetime.now().isoformat()
            self.save()
            logger.info(f"Updated balance for {user_id}: {amount} USDT")

    def ban_user(self, user_id: int, admin_id: int, reason: str = "Нарушение правил"):
        if self.user_exists(user_id):
            self.data["users"][str(user_id)]["banned"] = True
            self.data["users"][str(user_id)]["ban_info"] = {
                "by": admin_id,
                "at": datetime.now().isoformat(),
                "reason": reason
            }
            self.save()
            logger.info(f"User {user_id} banned by {admin_id}. Reason: {reason}")

    def unban_user(self, user_id: int):
        if self.user_exists(user_id):
            self.data["users"][str(user_id)]["banned"] = False
            self.data["users"][str(user_id)]["ban_info"] = None
            self.save()
            logger.info(f"User {user_id} unbanned")

    def search_user(self, query: str) -> Optional[Dict]:
        query = query.lower().strip('@')
        for user_id, user in self.data["users"].items():
            if (query in user["username"] or
                    query in user["display_name"].lower() or
                    query == user_id):
                return {"id": int(user_id), **user}
        return None

    def generate_id(self, prefix: str = "TX") -> str:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        return f"{prefix}{timestamp}-{random_str}"

# Инициализация
db = JSONDatabase()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Состояния FSM
class Form(StatesGroup):
    support_message = State()
    search_user = State()
    deal_amount = State()
    deal_description = State()
    confirm_deal = State()
    deposit_amount = State()
    withdraw_network = State()
    withdraw_amount = State()
    withdraw_address = State()
    dispute = State()
    admin_ban_user = State()
    admin_add_balance = State()
    admin_unban_user = State()
    deal_confirmation = State()

# Утилиты
async def show_main_menu(message: types.Message):
    builder = ReplyKeyboardBuilder()
    buttons = ["💰 Баланс", "🔍 Поиск пользователя", "ℹ️ Помощь", "🆘 Поддержка"]
    for btn in buttons:
        builder.add(types.KeyboardButton(text=btn))
    builder.adjust(2, 2)
    await message.answer(
        "Выберите действие:",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    logger.info(f"Showed main menu to {message.from_user.id}")

# Обработчики команд
@dp.message(CommandStart())
async def start(message: types.Message):
    user = message.from_user
    db.add_user(user.id, user.username or user.first_name)

    if db.get_user(user.id)["banned"]:
        logger.warning(f"Banned user tried to access: {user.id}")
        return await message.answer("⛔ Ваш аккаунт заблокирован администратором")

    welcome_text = (
        "🤝 <b>Добро пожаловать в Гарант-Бот!</b>\n\n"
        "Безопасные сделки между пользователями Telegram.\n\n"
        "✅ <b>Как это работает?</b>\n"
        "1. Найдите контрагента через поиск\n"
        "2. Создайте сделку с гарантией\n"
        "3. Средства блокируются на счете\n"
        "4. После выполнения условий - перевод продавцу\n\n"
        "<i>Без риска мошенничества!</i>"
    )
    await message.answer(welcome_text)
    await show_main_menu(message)
    logger.info(f"New session started for {user.id}")

@dp.message(F.text == "ℹ️ Помощь")
async def help_command(message: types.Message):
    help_text = (
        "🛠 <b>Помощь по боту-гаранту</b>\n\n"
        "🔹 <b>Основные команды:</b>\n"
        "/start - Перезапустить бота\n"
        "/help - Это сообщение\n\n"
        "💼 <b>Как работать со сделками:</b>\n"
        "1. Найдите пользователя через поиск\n"
        "2. Создайте сделку с описанием условий\n"
        "3. После выполнения условий обе стороны должны подтвердить завершение\n\n"
        "⚖️ <b>Если возникли проблемы:</b>\n"
        "Откройте спор командой <code>/dispute_XXXXXX</code>\n\n"
        "💳 <b>Управление балансом:</b>\n"
        f"• Минимальное пополнение: {MIN_DEPOSIT} USDT\n"
        f"• Минимальный вывод: {MIN_WITHDRAW} USDT\n\n"
        "📌 По всем вопросам обращайтесь в поддержку"
    )
    await message.answer(help_text)
    logger.info(f"Help requested by {message.from_user.id}")

@dp.message(F.text == "🆘 Поддержка")
async def support(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if user["banned"]:
        return await message.answer("⛔ Ваш аккаунт заблокирован")

    await message.answer(
        "✍️ Опишите ваш вопрос или проблему:\n\n"
        "Мы ответим в ближайшее время."
    )
    await state.set_state(Form.support_message)
    logger.info(f"Support requested by {message.from_user.id}")

@dp.message(Form.support_message)
async def process_support(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    support_text = message.text

    await bot.send_message(
        ADMIN_ID,
        f"📩 <b>НОВОЕ ОБРАЩЕНИЕ В ПОДДЕРЖКУ</b>\n\n"
        f"👤 Пользователь: @{user['username']}\n"
        f"🆔 ID: {message.from_user.id}\n\n"
        f"📝 Сообщение:\n<code>{support_text}</code>\n\n"
        f"Ответить: <a href='tg://user?id={message.from_user.id}'>Написать пользователю</a>"
    )

    await message.answer(
        "✅ Ваше сообщение отправлено!\n\n"
        "Мы ответим вам в ближайшее время."
    )
    await state.clear()
    logger.info(f"Support message from {message.from_user.id} forwarded to admin")


# =============================================
# БАЛАНС И ПЛАТЕЖИ
# =============================================

@dp.message(F.text == "💰 Баланс")
async def balance_menu(message: types.Message):
    user = db.get_user(message.from_user.id)
    if user["banned"]:
        return await message.answer("⛔ Ваш аккаунт заблокирован")

    builder = InlineKeyboardBuilder()
    builder.add(
        types.InlineKeyboardButton(text="💳 Пополнить", callback_data="deposit"),
        types.InlineKeyboardButton(text="📤 Вывести", callback_data="withdraw"),
        types.InlineKeyboardButton(text="📋 История", callback_data="history"),
        types.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    )
    builder.adjust(2, 1, 1)

    await message.answer(
        f"💰 <b>Ваш баланс:</b> {user['balance']:.2f} USDT\n\n"
        "Выберите действие:",
        reply_markup=builder.as_markup()
    )
    logger.info(f"Balance menu shown to {message.from_user.id}")


@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    await callback.message.delete()
    await show_main_menu(callback.message)
    await callback.answer()


@dp.callback_query(F.data == "history")
async def show_history(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user["banned"]:
        return await callback.answer("⛔ Ваш аккаунт заблокирован", show_alert=True)

    transactions = user.get("transactions", [])[:10]  # Последние 10 операций
    if not transactions:
        return await callback.answer("📭 История операций пуста", show_alert=True)

    history_text = "📋 <b>Последние операции:</b>\n\n"
    for tx_id in transactions:
        tx = db.data["transactions"].get(tx_id)
        if tx:
            date = datetime.fromisoformat(tx.get("created_at")).strftime("%d.%m.%Y %H:%M")
            amount = tx["amount"]
            type_ = "Пополнение" if amount > 0 else "Вывод"
            status = tx.get("status", "completed")
            history_text += (
                f"▫️ <b>{type_}</b>\n"
                f"Сумма: <code>{abs(amount):.2f} USDT</code>\n"
                f"Дата: <code>{date}</code>\n"
                f"Статус: <code>{status}</code>\n"
                f"ID: <code>{tx_id}</code>\n\n"
            )

    await callback.message.edit_text(
        history_text,
        reply_markup=InlineKeyboardBuilder()
        .add(types.InlineKeyboardButton(text="🔙 Назад", callback_data="balance_back"))
        .as_markup()
    )
    await callback.answer()


@dp.callback_query(F.data == "balance_back")
async def balance_back(callback: types.CallbackQuery):
    await balance_menu(callback.message)
    await callback.answer()


# =============================================
# ПОПОЛНЕНИЕ БАЛАНСА
# =============================================

@dp.callback_query(F.data == "deposit")
async def deposit_start(callback: types.CallbackQuery, state: FSMContext):
    if db.get_user(callback.from_user.id)["banned"]:
        return await callback.answer("⛔ Ваш аккаунт заблокирован", show_alert=True)

    await callback.message.answer(
        f"💳 <b>Введите сумму пополнения в USDT</b>\n"
        f"(Минимум: {MIN_DEPOSIT} USDT, максимум: 10000 USDT):",
        parse_mode="HTML"
    )
    await state.set_state(Form.deposit_amount)
    await callback.answer()


@dp.message(Form.deposit_amount)
async def process_deposit(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if user["banned"]:
        return await message.answer("⛔ Ваш аккаунт заблокирован")

    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount < MIN_DEPOSIT:
            return await message.answer(f"❌ Минимальная сумма {MIN_DEPOSIT} USDT")
        if amount > 10000:
            return await message.answer("❌ Максимальная сумма 10000 USDT")
    except ValueError:
        return await message.answer("❌ Введите корректную сумму (например: 50 или 100.50)")

    # Создаем счет на оплату через CryptoBot
    invoice = create_cryptobot_invoice(amount, message.from_user.id)
    if not invoice:
        return await message.answer("❌ Ошибка создания платежа. Попробуйте позже")

    tx_id = db.generate_id("DEP")
    db.data["transactions"][tx_id] = {
        "user_id": message.from_user.id,
        "amount": amount,
        "status": "pending",
        "invoice_id": invoice["invoice_id"],
        "created_at": datetime.now().isoformat(),
        "type": "deposit",
        "pay_url": invoice["pay_url"]
    }
    db.data["invoices"][invoice["invoice_id"]] = tx_id
    db.save()

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(
        text="✅ Проверить оплату",
        callback_data=f"check_deposit:{tx_id}"
    ))
    builder.add(types.InlineKeyboardButton(
        text="🔗 Открыть ссылку",
        url=invoice["pay_url"]
    ))

    await message.answer(
        f"💳 <b>Счет на оплату {amount:.2f} USDT</b>\n\n"
        f"🆔 <code>{tx_id}</code>\n"
        f"⏳ Счет действителен 15 минут\n\n"
        "После оплаты нажмите кнопку <b>Проверить оплату</b>",
        reply_markup=builder.as_markup(),
        disable_web_page_preview=True
    )
    await state.clear()
    logger.info(f"Deposit invoice created for {message.from_user.id}: {amount} USDT")


def create_cryptobot_invoice(amount: float, user_id: int):
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {
        "asset": "USDT",
        "amount": str(amount),
        "description": f"Пополнение баланса для {user_id}",
        "hidden_message": "Оплата гарант-бота",
        "paid_btn_name": "viewItem",
        "paid_btn_url": f"https://t.me/{BOT_TOKEN.split(':')[0]}",
        "payload": json.dumps({"user_id": user_id}),
        "allow_anonymous": False
    }
    try:
        response = requests.post(
            f"{CRYPTOBOT_API_URL}createInvoice",
            headers=headers,
            json=payload,
            timeout=10
        )
        if response.status_code == 200:
            result = response.json().get("result")
            if result and "pay_url" in result:
                return result
        logger.error(f"CryptoPay API error: {response.text}")
    except Exception as e:
        logger.error(f"CryptoPay connection error: {e}")
    return None


@dp.callback_query(F.data.startswith("check_deposit:"))
async def check_deposit_payment(callback: types.CallbackQuery):
    tx_id = callback.data.split(":")[1]
    tx = db.data["transactions"].get(tx_id)

    if not tx or tx["status"] != "pending":
        return await callback.answer("❌ Транзакция не найдена или уже обработана")

    status = check_invoice_status(tx["invoice_id"])
    if status == "paid":
        db.update_balance(tx["user_id"], tx["amount"])
        db.data["transactions"][tx_id]["status"] = "completed"
        db.data["transactions"][tx_id]["completed_at"] = datetime.now().isoformat()
        db.data["users"][str(tx["user_id"])]["transactions"].append(tx_id)
        db.save()

        await callback.message.edit_text(
            f"✅ <b>Баланс пополнен на {tx['amount']:.2f} USDT!</b>\n\n"
            f"🆔 <code>{tx_id}</code>\n"
            f"💳 Новый баланс: <code>{db.get_user(tx['user_id'])['balance']:.2f} USDT</code>"
        )
        logger.info(f"Deposit confirmed for {tx['user_id']}: {tx['amount']} USDT")
    elif status == "active":
        await callback.answer("⌛ Платеж еще не подтвержден", show_alert=True)
    else:
        await callback.answer("❌ Платеж не найден или срок действия истек", show_alert=True)


def check_invoice_status(invoice_id: int) -> Optional[str]:
    try:
        response = requests.get(
            f"{CRYPTOBOT_API_URL}getInvoices?invoice_ids={invoice_id}",
            headers={"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN},
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("result", {}).get("items", [{}])[0].get("status")
        logger.error(f"CryptoPay check error: {response.text}")
    except Exception as e:
        logger.error(f"CryptoPay connection error: {e}")
    return None


# =============================================
# ВЫВОД СРЕДСТВ
# =============================================

@dp.callback_query(F.data == "withdraw")
async def withdraw_start(callback: types.CallbackQuery, state: FSMContext):
    user = db.get_user(callback.from_user.id)
    if user["banned"]:
        return await callback.answer("⛔ Ваш аккаунт заблокирован", show_alert=True)

    if user["balance"] < MIN_WITHDRAW:
        return await callback.answer(
            f"❌ Минимальная сумма вывода {MIN_WITHDRAW} USDT",
            show_alert=True
        )

    builder = InlineKeyboardBuilder()
    networks = ["TRC20", "ERC20", "BSC"]
    for net in networks:
        builder.add(types.InlineKeyboardButton(
            text=net,
            callback_data=f"withdraw_net:{net}"
        ))
    builder.adjust(3)

    await callback.message.edit_text(
        "🌐 <b>Выберите сеть для вывода:</b>\n\n"
        "⚠️ <i>Убедитесь, что выбранная сеть поддерживает USDT</i>",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("withdraw_net:"))
async def select_withdraw_network(callback: types.CallbackQuery, state: FSMContext):
    network = callback.data.split(":")[1]
    await state.update_data(network=network)
    user = db.get_user(callback.from_user.id)

    await callback.message.answer(
        f"🌐 <b>Выбрана сеть:</b> {network}\n"
        f"💰 <b>Доступно:</b> {user['balance']:.2f} USDT\n\n"
        f"Введите сумму для вывода (мин. {MIN_WITHDRAW} USDT):"
    )
    await state.set_state(Form.withdraw_amount)
    await callback.answer()


@dp.message(Form.withdraw_amount)
async def process_withdraw_amount(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if user["banned"]:
        return await message.answer("⛔ Ваш аккаунт заблокирован")

    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount < MIN_WITHDRAW:
            return await message.answer(f"❌ Минимальная сумма {MIN_WITHDRAW} USDT")
        if amount > user["balance"]:
            return await message.answer(
                f"❌ Недостаточно средств. Доступно: {user['balance']:.2f} USDT"
            )
    except ValueError:
        return await message.answer("❌ Введите корректную сумму (например: 50 или 100.50)")

    await state.update_data(amount=amount)
    await message.answer(
        "📭 Введите адрес кошелька для получения средств:\n\n"
        "⚠️ <i>Проверьте правильность адреса! Ошибки могут привести к потере средств.</i>"
    )
    await state.set_state(Form.withdraw_address)


@dp.message(Form.withdraw_address)
async def process_withdraw_address(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if user["banned"]:
        return await message.answer("⛔ Ваш аккаунт заблокирован")

    address = message.text.strip()
    data = await state.get_data()
    amount = data["amount"]
    network = data["network"]

    # Базовая валидация адреса
    if network in ["TRC20", "ERC20"] and not (address.startswith("0x") and len(address) == 42):
        return await message.answer(
            "❌ Неверный формат адреса. Должен начинаться с 0x и содержать 42 символа"
        )

    # Создаем запрос на вывод
    tx_id = db.generate_id("WTH")
    db.data["transactions"][tx_id] = {
        "user_id": message.from_user.id,
        "amount": -amount,
        "address": address,
        "network": network,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "type": "withdraw"
    }
    db.update_balance(message.from_user.id, -amount)
    db.save()

    # Уведомление администратору
    await bot.send_message(
        ADMIN_ID,
        f"🆘 <b>НОВЫЙ ЗАПРОС НА ВЫВОД</b>\n\n"
        f"👤 Пользователь: @{user['username']}\n"
        f"🆔 ID: {message.from_user.id}\n"
        f"💵 Сумма: {amount:.2f} USDT\n"
        f"🌐 Сеть: {network}\n"
        f"📭 Адрес: <code>{address}</code>\n\n"
        f"🆔 ID транзакции: <code>{tx_id}</code>\n\n"
        f"Для подтверждения: /approve_{tx_id}\n"
        f"Для отмены: /reject_{tx_id}"
    )

    await message.answer(
        "✅ <b>Запрос на вывод отправлен!</b>\n\n"
        "Обычно обработка занимает до 24 часов.\n"
        "Вы получите уведомление о статусе.\n\n"
        f"🆔 <code>{tx_id}</code>"
    )
    await state.clear()
    logger.info(f"Withdraw request from {message.from_user.id}: {amount} USDT to {address}")


@dp.message(F.text.startswith("/approve_"))
async def approve_withdraw(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ Доступ запрещён")

    tx_id = message.text.split("_")[1].strip()
    tx = db.data["transactions"].get(tx_id)

    if not tx or tx["status"] != "pending":
        return await message.answer("❌ Транзакция не найдена или уже обработана")

    tx["status"] = "completed"
    tx["approved_by"] = message.from_user.id
    tx["approved_at"] = datetime.now().isoformat()
    db.save()

    await message.answer(f"✅ Вывод {tx_id} подтверждён")
    await bot.send_message(
        tx["user_id"],
        f"✅ Ваш вывод на сумму {abs(tx['amount'])} USDT выполнен\n"
        f"🌐 Сеть: {tx['network']}\n"
        f"📭 Адрес: {tx['address']}\n"
        f"🆔 Транзакция: {tx_id}"
    )


@dp.message(F.text.startswith("/reject_"))
async def reject_withdraw(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ Доступ запрещён")

    tx_id = message.text.split("_")[1].strip()
    tx = db.data["transactions"].get(tx_id)

    if not tx or tx["status"] != "pending":
        return await message.answer("❌ Транзакция не найдена или уже обработана")

    # Возвращаем средства на баланс
    db.update_balance(tx["user_id"], abs(tx["amount"]))
    tx["status"] = "rejected"
    tx["rejected_by"] = message.from_user.id
    tx["rejected_at"] = datetime.now().isoformat()
    db.save()

    await message.answer(f"❌ Вывод {tx_id} отклонён")
    await bot.send_message(
        tx["user_id"],
        f"❌ Ваш вывод отклонён. Средства возвращены на баланс\n"
        f"🆔 Транзакция: {tx_id}\n"
        f"💳 Текущий баланс: {db.get_user(tx['user_id'])['balance']} USDT"
    )


# =============================================
# СДЕЛКИ И ГАРАНТИИ
# =============================================

@dp.message(F.text == "🔍 Поиск пользователя")
async def search_user(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if user["banned"]:
        return await message.answer("⛔ Ваш аккаунт заблокирован")

    await message.answer(
        "🔍 <b>Введите @username или ID пользователя:</b>\n\n"
        "<i>Примеры:\n"
        "@username\n"
        "123456789</i>",
        parse_mode="HTML"
    )
    await state.set_state(Form.search_user)
    logger.info(f"User search initiated by {message.from_user.id}")


@dp.message(Form.search_user)
async def process_search(message: types.Message, state: FSMContext):
    query = message.text.strip()
    found_user = db.search_user(query)

    if not found_user:
        return await message.answer(
            "❌ Пользователь не найден.\n"
            "Проверьте правильность ввода и попробуйте снова."
        )

    if found_user["banned"]:
        return await message.answer("⚠️ Этот пользователь заблокирован в системе")

    if found_user["id"] == message.from_user.id:
        return await message.answer("❌ Нельзя создать сделку с самим собой")

    await state.update_data(
        target_user_id=found_user["id"],
        target_username=found_user["display_name"]
    )

    await message.answer(
        f"✅ <b>Найден пользователь:</b> {found_user['display_name']}\n"
        f"🆔 ID: <code>{found_user['id']}</code>\n\n"
        "Введите сумму сделки в USDT:",
        parse_mode="HTML"
    )
    await state.set_state(Form.deal_amount)
    logger.info(f"User found: {found_user['id']} for {message.from_user.id}")


@dp.message(Form.deal_amount)
async def process_deal_amount(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if user["banned"]:
        return await message.answer("⛔ Ваш аккаунт заблокирован")

    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0:
            return await message.answer("❌ Сумма должна быть больше 0")
        if amount > user["balance"]:
            return await message.answer(
                f"❌ Недостаточно средств. Ваш баланс: {user['balance']:.2f} USDT\n\n"
                f"Пополните баланс или введите меньшую сумму."
            )
    except ValueError:
        return await message.answer("❌ Введите корректную сумму (например: 50 или 100.50)")

    await state.update_data(deal_amount=amount)
    await message.answer(
        "📝 <b>Опишите условия сделки:</b>\n\n"
        "• Что обмениваете\n"
        "• Обязательства сторон\n"
        "• Сроки выполнения\n"
        "• Другие важные условия\n\n"
        "<i>Минимум 30 символов</i>",
        parse_mode="HTML"
    )
    await state.set_state(Form.deal_description)


@dp.message(Form.deal_description)
async def process_deal_description(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if user["banned"]:
        return await message.answer("⛔ Ваш аккаунт заблокирован")

    description = message.text.strip()
    if len(description) < 30:
        return await message.answer("❌ Описание должно быть подробным (минимум 30 символов)")

    data = await state.get_data()
    deal_id = db.generate_id("DL")

    db.data["deals"][deal_id] = {
        "from_user_id": message.from_user.id,
        "from_username": user["display_name"],
        "to_user_id": data["target_user_id"],
        "to_username": data["target_username"],
        "amount": data["deal_amount"],
        "description": description,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "from_confirmed": False,
        "to_confirmed": False,
        "messages": []
    }
    db.save()

    builder = InlineKeyboardBuilder()
    builder.add(
        types.InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"deal_confirm:{deal_id}"),
        types.InlineKeyboardButton(text="❌ Отменить", callback_data=f"deal_cancel:{deal_id}")
    )

    await message.answer(
        f"📄 <b>Сделка #{deal_id}</b>\n\n"
        f"👤 <b>Кому:</b> {data['target_username']}\n"
        f"💰 <b>Сумма:</b> {data['deal_amount']:.2f} USDT\n\n"
        f"📝 <b>Условия:</b>\n{description}\n\n"
        "<b>Подтвердите создание сделки:</b>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(Form.confirm_deal)
    logger.info(f"Deal {deal_id} created by {message.from_user.id}")


@dp.callback_query(F.data.startswith("deal_confirm:"))
async def confirm_deal(callback: types.CallbackQuery, state: FSMContext):
    deal_id = callback.data.split(":")[1]
    deal = db.data["deals"].get(deal_id)

    if not deal or deal["status"] != "pending":
        await callback.answer("❌ Сделка не найдена или уже обработана", show_alert=True)
        return

    user = db.get_user(callback.from_user.id)
    if user["banned"]:
        await callback.answer("⛔ Ваш аккаунт заблокирован", show_alert=True)
        return

    # Блокируем средства
    db.update_balance(deal["from_user_id"], -deal["amount"])
    deal["status"] = "active"
    db.data["users"][str(deal["from_user_id"])]["deals"].append(deal_id)
    db.save()

    # Уведомление инициатору
    try:
        await callback.message.edit_text("✅ Сделка создана! Средства зарезервированы.")
        await bot.send_message(
            deal["from_user_id"],
            f"📌 <b>Сделка #{deal_id} создана!</b>\n\n"
            f"👤 <b>Для:</b> {deal['to_username']}\n"
            f"💰 <b>Сумма:</b> {deal['amount']:.2f} USDT\n\n"
            f"📝 <b>Условия:</b>\n{deal['description']}\n\n"
            "<i>Для завершения обе стороны должны подтвердить выполнение условий:</i>\n"
            f"<code>/confirm_deal_{deal_id}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error sending deal notification: {e}")

    # Уведомление получателю
    try:
        await bot.send_message(
            deal["to_user_id"],
            f"🔔 <b>Вам предложена новая сделка #{deal_id}</b>\n\n"
            f"👤 <b>От:</b> {deal['from_username']}\n"
            f"💰 <b>Сумма:</b> {deal['amount']:.2f} USDT\n\n"
            f"📝 <b>Условия:</b>\n{deal['description']}\n\n"
            "<i>Для завершения обе стороны должны подтвердить выполнение условий:</i>\n"
            f"<code>/confirm_deal_{deal_id}</code>\n\n"
            "<i>Если есть проблемы, откройте спор:</i>\n"
            f"<code>/dispute_{deal_id}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error sending deal notification to target: {e}")

    await callback.answer()
    await state.clear()
    logger.info(f"Deal {deal_id} confirmed by {callback.from_user.id}")


@dp.message(F.text.startswith("/confirm_deal_"))
async def confirm_deal_completion(message: types.Message):
    user = db.get_user(message.from_user.id)
    if user["banned"]:
        return await message.answer("⛔ Ваш аккаунт заблокирован")

    try:
        deal_id = message.text.split("_")[-1].strip()
        deal = db.data["deals"].get(deal_id)

        if not deal or deal["status"] != "active":
            return await message.answer("❌ Сделка не найдена или уже завершена")

        if message.from_user.id == deal["from_user_id"]:
            deal["from_confirmed"] = True
        elif message.from_user.id == deal["to_user_id"]:
            deal["to_confirmed"] = True
        else:
            return await message.answer("❌ Вы не участник этой сделки")

        db.save()

        # Проверяем, подтвердили ли обе стороны
        if deal["from_confirmed"] and deal["to_confirmed"]:
            # Переводим средства получателю
            db.update_balance(deal["to_user_id"], deal["amount"])
            deal["status"] = "completed"
            deal["completed_at"] = datetime.now().isoformat()
            db.save()

            # Уведомляем участников
            await message.answer(
                f"✅ <b>Сделка #{deal_id} успешно завершена!</b>\n\n"
                f"💰 Сумма: {deal['amount']:.2f} USDT переведена получателю."
            )

            other_user_id = deal["to_user_id"] if message.from_user.id == deal["from_user_id"] else deal["from_user_id"]
            try:
                await bot.send_message(
                    other_user_id,
                    f"✅ <b>Сделка #{deal_id} успешно завершена!</b>\n\n"
                    f"💰 Сумма: {deal['amount']:.2f} USDT переведена получателю.\n"
                    f"💳 Ваш баланс: {db.get_user(other_user_id)['balance']:.2f} USDT"
                )
            except Exception as e:
                logger.error(f"Error notifying user {other_user_id}: {e}")
        else:
            await message.answer(
                "✅ Ваше подтверждение получено. Ожидаем подтверждения второй стороны."
            )

    except Exception as e:
        logger.error(f"Deal confirmation error: {e}")
        await message.answer("❌ Ошибка подтверждения сделки")


@dp.message(F.text.startswith("/dispute_"))
async def open_dispute(message: types.Message):
    user = db.get_user(message.from_user.id)
    if user["banned"]:
        return await message.answer("⛔ Ваш аккаунт заблокирован")

    try:
        deal_id = message.text.split("_")[1].strip()
        deal = db.data["deals"].get(deal_id)

        if not deal:
            return await message.answer("❌ Сделка не найдена")

        if message.from_user.id not in [deal["from_user_id"], deal["to_user_id"]]:
            return await message.answer("❌ Вы не участник этой сделки")

        deal["status"] = "dispute"
        db.save()

        await bot.send_message(
            ADMIN_ID,
            f"⚖️ <b>СПОР ПО СДЕЛКЕ #{deal_id}</b>\n\n"
            f"👤 Инициатор: {deal['from_username']} (ID: {deal['from_user_id']})\n"
            f"👤 Получатель: {deal['to_username']} (ID: {deal['to_user_id']})\n"
            f"💰 Сумма: {deal['amount']:.2f} USDT\n\n"
            f"📝 Условия сделки:\n{deal['description']}\n\n"
            f"👤 Открыл спор: @{user['username']} (ID: {message.from_user.id})\n\n"
            f"<b>Для разрешения спора:</b>\n"
            f"/resolve_{deal_id} [ID_победителя] [комментарий]\n\n"
            f"Пример:\n"
            f"/resolve_{deal_id} {deal['from_user_id']} Условия выполнены"
        )

        await message.answer(
            "⚖️ <b>Спор успешно открыт!</b>\n\n"
            "Администратор рассмотрит ваш спор в ближайшее время.\n"
            "Вы получите уведомление о решении."
        )

    except Exception as e:
        logger.error(f"Dispute error: {e}")
        await message.answer("❌ Ошибка. Используйте: /dispute_ID_сделки")


@dp.message(F.text.startswith("/resolve_"))
async def resolve_dispute(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ Доступ запрещён")

    try:
        parts = message.text.split()
        deal_id = parts[0].split("_")[1]
        winner_id = int(parts[1])
        comment = " ".join(parts[2:]) if len(parts) > 2 else "Решение администратора"

        deal = db.data["deals"].get(deal_id)
        if not deal or deal["status"] != "dispute":
            return await message.answer("❌ Сделка не найдена или статус не 'dispute'")

        if winner_id not in [deal["from_user_id"], deal["to_user_id"]]:
            return await message.answer("❌ Указан неверный ID победителя")

        # Переводим средства победителю
        db.update_balance(winner_id, deal["amount"])
        deal["status"] = "resolved"
        deal["resolution"] = {
            "by": message.from_user.id,
            "at": datetime.now().isoformat(),
            "winner": winner_id,
            "comment": comment
        }
        db.save()

        # Уведомляем участников
        for user_id in [deal["from_user_id"], deal["to_user_id"]]:
            try:
                await bot.send_message(
                    user_id,
                    f"⚖️ <b>Спор по сделке #{deal_id} разрешён</b>\n\n"
                    f"🏆 Победитель: {'Вы' if user_id == winner_id else f'Пользователь {winner_id}'}\n"
                    f"📝 Комментарий: {comment}\n"
                    f"💰 Сумма: {deal['amount']:.2f} USDT\n\n"
                    f"💳 Текущий баланс: {db.get_user(user_id)['balance']:.2f} USDT"
                )
            except Exception as e:
                logger.error(f"Can't notify user {user_id}: {e}")

        await message.answer(
            "✅ <b>Спор успешно разрешён</b>\n\n"
            f"🏆 Победитель: {winner_id}\n"
            f"📝 Комментарий: {comment}"
        )

    except Exception as e:
        logger.error(f"Resolve dispute error: {e}")
        await message.answer(
            "❌ Ошибка. Формат команды:\n"
            "/resolve_ID_сделки ID_победителя [комментарий]\n\n"
            "Пример:\n"
            f"/resolve_{deal_id} {deal['from_user_id']} Условия выполнены"
        )


# =============================================
# АДМИН ПАНЕЛЬ
# =============================================

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        logger.warning(f"Unauthorized admin access attempt: {message.from_user.id}")
        return await message.answer("❌ Доступ запрещен")

    builder = InlineKeyboardBuilder()
    builder.add(
        types.InlineKeyboardButton(text="🔨 Забанить", callback_data="admin:ban"),
        types.InlineKeyboardButton(text="🔓 Разбанить", callback_data="admin:unban"),
        types.InlineKeyboardButton(text="💰 Изменить баланс", callback_data="admin:balance"),
        types.InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
        types.InlineKeyboardButton(text="🔄 Выплаты", callback_data="admin:withdrawals"),
        types.InlineKeyboardButton(text="⚖️ Активные споры", callback_data="admin:disputes")
    )
    builder.adjust(2, 2, 1, 1)

    await message.answer(
        "🛠 <b>Админ-панель:</b>",
        reply_markup=builder.as_markup()
    )
    logger.info(f"Admin panel accessed by {message.from_user.id}")


@dp.callback_query(F.data.startswith("admin:"))
async def admin_actions(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("❌ Доступ запрещен", show_alert=True)

    action = callback.data.split(":")[1]

    if action == "ban":
        await callback.message.answer(
            "Введите ID пользователя и причину через пробел:\n"
            "<code>123456789 Нарушение правил</code>",
            parse_mode="HTML"
        )
        await state.set_state(Form.admin_ban_user)
    elif action == "unban":
        await callback.message.answer(
            "Введите ID пользователя для разбана:\n"
            "<code>123456789</code>",
            parse_mode="HTML"
        )
        await state.set_state(Form.admin_unban_user)
    elif action == "balance":
        await callback.message.answer(
            "Введите ID и сумму через пробел (для снятия укажите минус):\n"
            "<code>123456789 100.50</code>\n"
            "<code>123456789 -50.00</code>",
            parse_mode="HTML"
        )
        await state.set_state(Form.admin_add_balance)
    elif action == "stats":
        stats = await get_system_stats()
        await callback.message.edit_text(stats, parse_mode="HTML")
    elif action == "withdrawals":
        withdrawals = await get_pending_withdrawals()
        await callback.message.edit_text(withdrawals, parse_mode="HTML")
    elif action == "disputes":
        disputes = await get_active_disputes()
        await callback.message.edit_text(disputes, parse_mode="HTML")

    await callback.answer()


@dp.message(Form.admin_ban_user)
async def process_ban_user(message: types.Message, state: FSMContext):
    try:
        parts = message.text.split()
        user_id = int(parts[0])
        reason = " ".join(parts[1:]) if len(parts) > 1 else "Нарушение правил"

        if not db.user_exists(user_id):
            return await message.answer("❌ Пользователь не найден")

        db.ban_user(user_id, message.from_user.id, reason)

        try:
            await bot.send_message(
                user_id,
                f"⛔ <b>Ваш аккаунт заблокирован администратором</b>\n\n"
                f"📝 Причина: {reason}\n\n"
                f"По вопросам обращайтесь в поддержку."
            )
        except Exception as e:
            logger.error(f"Can't notify banned user {user_id}: {e}")

        await message.answer(
            f"✅ Пользователь {user_id} заблокирован\n"
            f"Причина: {reason}"
        )
    except Exception as e:
        logger.error(f"Ban user error: {e}")
        await message.answer("❌ Ошибка. Формат: ID_пользователя [причина]")
    finally:
        await state.clear()


@dp.message(Form.admin_unban_user)
async def process_unban_user(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())

        if not db.user_exists(user_id):
            return await message.answer("❌ Пользователь не найден")

        db.unban_user(user_id)

        try:
            await bot.send_message(
                user_id,
                "✅ <b>Ваш аккаунт разблокирован администратором</b>\n\n"
                "Теперь вы снова можете пользоваться ботом."
            )
        except Exception as e:
            logger.error(f"Can't notify unbanned user {user_id}: {e}")

        await message.answer(f"✅ Пользователь {user_id} разблокирован")
    except Exception as e:
        logger.error(f"Unban user error: {e}")
        await message.answer("❌ Ошибка. Введите ID пользователя")
    finally:
        await state.clear()


@dp.message(Form.admin_add_balance)
async def process_add_balance(message: types.Message, state: FSMContext):
    try:
        parts = message.text.split()
        user_id = int(parts[0])
        amount = float(parts[1])

        if not db.user_exists(user_id):
            return await message.answer("❌ Пользователь не найден")

        db.update_balance(user_id, amount)
        user = db.get_user(user_id)

        try:
            await bot.send_message(
                user_id,
                f"ℹ️ <b>Ваш баланс изменён администратором</b>\n\n"
                f"💰 Изменение: {amount:.2f} USDT\n"
                f"💳 Новый баланс: {user['balance']:.2f} USDT"
            )
        except Exception as e:
            logger.error(f"Can't notify user {user_id}: {e}")

        await message.answer(
            f"✅ Баланс пользователя {user_id} изменён\n"
            f"💰 Изменение: {amount:.2f} USDT\n"
            f"💳 Новый баланс: {user['balance']:.2f} USDT"
        )
    except Exception as e:
        logger.error(f"Add balance error: {e}")
        await message.answer("❌ Ошибка. Формат: ID_пользователя сумма")
    finally:
        await state.clear()


async def get_system_stats() -> str:
    total_users = len(db.data["users"])
    active_users = len([u for u in db.data["users"].values() if not u["banned"]])
    total_balance = sum(u["balance"] for u in db.data["users"].values())
    total_deals = len(db.data["deals"])
    active_deals = len([d for d in db.data["deals"].values() if d["status"] == "active"])
    pending_withdrawals = len(
        [t for t in db.data["transactions"].values() if t["type"] == "withdraw" and t["status"] == "pending"])

    return (
        "📊 <b>Системная статистика</b>\n\n"
        f"👥 <b>Пользователи:</b> {total_users} ({active_users} активных)\n"
        f"💰 <b>Общий баланс:</b> {total_balance:.2f} USDT\n"
        f"🤝 <b>Сделки:</b> {total_deals} ({active_deals} активных)\n"
        f"🔄 <b>Ожидают выплаты:</b> {pending_withdrawals}\n\n"
        f"🕒 <b>Последнее обновление:</b>\n{datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )


async def get_pending_withdrawals() -> str:
    pending = [
                  t for t in db.data["transactions"].values()
                  if t["type"] == "withdraw" and t["status"] == "pending"
              ][:10]  # Последние 10

    if not pending:
        return "⏳ Нет ожидающих запросов на вывод"

    text = "📋 <b>Ожидающие выплаты:</b>\n\n"
    for tx in pending:
        user = db.get_user(tx["user_id"])
        text += (
            f"👤 @{user['username']} (ID: {tx['user_id']})\n"
            f"💰 {abs(tx['amount']):.2f} USDT ({tx['network']})\n"
            f"📭 {tx['address']}\n"
            f"🆔 TX: <code>{tx['id']}</code>\n"
            f"⏳ {datetime.fromisoformat(tx['created_at']).strftime('%d.%m %H:%M')}\n\n"
            f"Для подтверждения: /approve_{tx['id']}\n"
            f"Для отмены: /reject_{tx['id']}\n\n"
        )
    return text


async def get_active_disputes() -> str:
    disputes = [
                   d for d in db.data["deals"].values()
                   if d["status"] == "dispute"
               ][:10]  # Последние 10

    if not disputes:
        return "⚖️ Нет активных споров"

    text = "⚖️ <b>Активные споры:</b>\n\n"
    for deal in disputes:
        text += (
            f"🆔 <code>{deal['id']}</code>\n"
            f"👤 От: {deal['from_username']} (ID: {deal['from_user_id']})\n"
            f"👤 Кому: {deal['to_username']} (ID: {deal['to_user_id']})\n"
            f"💰 Сумма: {deal['amount']:.2f} USDT\n\n"
            f"Для разрешения:\n"
            f"/resolve_{deal['id']} [ID_победителя] [комментарий]\n\n"
        )
    return text


# =============================================
# ЗАВЕРШАЮЩИЕ ФУНКЦИИ
# =============================================

async def on_startup():
    logger.info("Bot starting...")
    # Создаем папку для логов, если ее нет
    if not os.path.exists("logs"):
        os.makedirs("logs")


async def on_shutdown():
    logger.info("Bot shutting down...")
    await bot.session.close()


async def check_pending_payments():
    """Фоновая задача для проверки платежей"""
    while True:
        try:
            pending = [
                tx_id for tx_id, tx in db.data["transactions"].items()
                if tx["type"] == "deposit" and tx["status"] == "pending"
            ]

            for tx_id in pending:
                tx = db.data["transactions"][tx_id]
                status = check_invoice_status(tx["invoice_id"])

                if status == "paid":
                    db.update_balance(tx["user_id"], tx["amount"])
                    db.data["transactions"][tx_id]["status"] = "completed"
                    db.data["transactions"][tx_id]["completed_at"] = datetime.now().isoformat()
                    db.data["users"][str(tx["user_id"])]["transactions"].append(tx_id)
                    db.save()

                    try:
                        await bot.send_message(
                            tx["user_id"],
                            f"✅ <b>Платеж подтвержден!</b>\n\n"
                            f"💰 Сумма: {tx['amount']:.2f} USDT\n"
                            f"🆔 ID: <code>{tx_id}</code>\n"
                            f"💳 Текущий баланс: {db.get_user(tx['user_id'])['balance']:.2f} USDT"
                        )
                    except Exception as e:
                        logger.error(f"Error sending payment confirmation: {e}")

                elif status == "expired":
                    db.data["transactions"][tx_id]["status"] = "expired"
                    db.save()

            await asyncio.sleep(60)  # Проверка каждую минуту

        except Exception as e:
            logger.error(f"Error in payment check task: {e}")
            await asyncio.sleep(60)


async def main():
    await on_startup()

    # Запускаем фоновые задачи
    asyncio.create_task(check_pending_payments())

    try:
        await dp.start_polling(bot)
    finally:
        await on_shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
    finally:
        logger.info("Bot shutdown complete")