"""Russian Qt dialog commands without optional translation resource files."""
from PySide6.QtCore import QTranslator

_BUTTONS = {
    "OK": "ОК", "Yes": "Да", "No": "Нет", "Cancel": "Отмена",
    "Close": "Закрыть", "Save": "Сохранить", "Open": "Открыть",
    "Discard": "Не сохранять", "Apply": "Применить", "Reset": "Сбросить",
    "Retry": "Повторить", "Abort": "Прервать", "Ignore": "Пропустить",
    "Help": "Справка", "Yes to All": "Да для всех", "No to All": "Нет для всех",
    "Save All": "Сохранить всё", "Restore Defaults": "По умолчанию",
}


class RussianButtons(QTranslator):
    def isEmpty(self):
        return False

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        if context in {"QPlatformTheme", "QMessageBox", "QDialogButtonBox"}:
            return _BUTTONS.get(sourceText.replace("&", ""))
        # A null QString preserves other Qt translations and original text.
        return None


def install_dialog_translation(app):
    if not hasattr(app, "_reporter_dialog_translator"):
        app._reporter_dialog_translator = RussianButtons(app)
        app.installTranslator(app._reporter_dialog_translator)
