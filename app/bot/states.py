from aiogram.fsm.state import State, StatesGroup


class LoginFlow(StatesGroup):
    invite = State()
    login = State()
    password = State()


class FeedbackFlow(StatesGroup):
    text = State()


class SettingsFlow(StatesGroup):
    time = State()
    name = State()


class AdminFlow(StatesGroup):
    reply = State()
