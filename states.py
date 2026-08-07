from aiogram.fsm.state import State, StatesGroup


class AuthStates(StatesGroup):
    waiting_login = State()
    waiting_password = State()


class InnStates(StatesGroup):
    waiting_inn = State()


class AttachStates(StatesGroup):
    choosing_days = State()
    confirm = State()


class AddStates(StatesGroup):
    waiting_client_name = State()
    waiting_geo = State()
    waiting_address = State()
    waiting_phone = State()
    choosing_region = State()
    choosing_oblast = State()
    choosing_okrug = State()
    choosing_rayon = State()
    choosing_format = State()
    choosing_channel = State()
    choosing_type = State()
    choosing_category = State()
    choosing_delivery = State()
    choosing_days = State()
    waiting_comments = State()
    confirm = State()
