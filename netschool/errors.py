"""Исключения клиента NetSchool."""


class NetSchoolError(Exception):
    """Общая ошибка: неожиданный ответ сервера, ошибка в JSON и т.п."""


class LoginError(NetSchoolError):
    """Неверный логин/пароль или сервер не выдал AT.

    НЕ повторяй вход автоматически: после нескольких неудачных попыток
    NetSchool может заблокировать учётную запись.

    status — HTTP-код ответа postlogin.asp, server_message — текст ошибки от сервера (если был).
    Ответ 4xx с сообщением почти наверняка значит «неверный логин или пароль».
    """

    def __init__(self, message: str = "", *, status: int | None = None, server_message: str | None = None):
        super().__init__(message)
        self.status = status
        self.server_message = server_message

    @property
    def bad_credentials(self) -> bool:
        return bool(self.server_message) and self.status is not None and 400 <= self.status < 500


class SessionExpired(NetSchoolError):
    """Сессия (AT) больше не действует — нужно войти заново."""
