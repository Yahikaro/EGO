"""EGO-Setup.exe: a typical Windows setup wizard (welcome, folder, components, install, finish).

    EGO-Setup.exe                                     the wizard
    EGO-Setup.exe --silent --dir D:\\EGO [--llama] [--cloudflared] [--tts] [--no-shortcuts]
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer_core as core  # noqa: E402


def ui_is_russian() -> bool:
    try:
        return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3ff) == 0x19
    except (AttributeError, OSError):
        return False


RU = ui_is_russian()


def t(ru: str, en: str) -> str:
    return ru if RU else en


def run_install(root: Path, options: dict, report=lambda fraction, text: None, log=print) -> list[str]:
    """Do everything the wizard asks for. Returns the list of things that did not work."""
    problems: list[str] = []
    zip_path = core.payload_path()
    report(0.0, t('Копирование файлов…', 'Copying files…'))
    count = core.extract(zip_path, root, lambda f, name='': report(f * 0.6, name))
    log(f'{count} files')
    size = core.payload_size(zip_path)
    report(0.62, t('Ярлыки и запись в «Приложения»…', 'Shortcuts and the Apps entry…'))
    if options.get('shortcuts', True) and not core.make_shortcuts(root, options.get('desktop', False), True):
        problems.append(t('ярлыки', 'shortcuts'))
    if not core.register_uninstaller(root, size):
        problems.append(t('запись для удаления в «Приложениях»', 'Apps entry for uninstalling'))
    if options.get('llama'):
        report(0.65, t('Скачивание llama.cpp (запуск моделей)…', 'Downloading llama.cpp (runs the models)…'))
        if not core.install_llama(root, log, lambda f: report(0.65 + 0.2 * f, '')):
            problems.append('llama.cpp')
    if options.get('cloudflared'):
        report(0.86, t('Скачивание cloudflared (туннель)…', 'Downloading cloudflared (tunnel)…'))
        if not core.install_cloudflared(root, log, lambda f: report(0.86 + 0.04 * f, '')):
            problems.append('cloudflared')
    if options.get('tts'):
        report(0.9, t('Установка озвучки (долго, несколько ГБ)…', 'Installing voice output (long, several GB)…'))
        python = core.find_python312()
        if not python or not core.install_tts(root, python, log):
            problems.append(t('озвучка', 'voice output'))
    report(1.0, t('Готово', 'Done'))
    return problems


def silent(args) -> int:
    root = Path(args.dir) if args.dir else core.default_dir()
    code = core.check_target(root)
    if code in ('EMPTY', 'NOT_WRITABLE'):
        print('FAIL', code)
        return 2
    problems = run_install(root, {'shortcuts': not args.no_shortcuts, 'desktop': args.desktop, 'llama': args.llama,
                                  'cloudflared': args.cloudflared, 'tts': args.tts},
                           lambda f, text: None, lambda line: print(line, flush=True))
    print('PASS' if not problems else 'PARTIAL ' + ', '.join(problems))
    return 0


def wizard() -> int:
    from PySide6.QtCore import Qt, QThread, Signal, QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import (QApplication, QWizard, QWizardPage, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                                   QPushButton, QCheckBox, QProgressBar, QPlainTextEdit, QFileDialog, QMessageBox)
    from windows_ui.theme import stylesheet

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(stylesheet('dark'))
    icon_path = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'EGO.ico'
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    def label(text, muted=False, heading=False):
        widget = QLabel(text)
        widget.setWordWrap(True)
        widget.setTextFormat(Qt.TextFormat.RichText)
        if muted:
            widget.setObjectName('muted')
        if heading:
            widget.setObjectName('heading')
        return widget

    class Worker(QThread):
        progress = Signal(int, str)
        line = Signal(str)
        done = Signal(list)

        def __init__(self, root, options):
            super().__init__()
            self.root, self.options = root, options

        def run(self):
            try:
                problems = run_install(self.root, self.options, lambda f, text: self.progress.emit(int(f * 100), text),
                                       lambda text: self.line.emit(str(text)))
            except Exception as exc:                                  # nothing is left half-explained
                self.line.emit(f'ERROR: {exc}')
                problems = [t('копирование файлов', 'copying files')]
            self.done.emit(problems)

    class Welcome(QWizardPage):
        def __init__(self):
            super().__init__()
            self.setTitle(t('Установка EGO', 'EGO setup'))
            layout = QVBoxLayout(self)
            layout.addWidget(label(t('Мастер установит EGO — локальное приложение для ролевых историй с языковой моделью на вашей видеокарте.',
                                     'This wizard installs EGO, a local app for roleplay stories with a language model on your graphics card.')))
            layout.addWidget(label(t('Всё работает на вашем компьютере: истории хранятся в зашифрованной базе и никуда не отправляются. '
                                     'Права администратора не нужны.',
                                     'Everything runs on your computer: stories live in an encrypted database and are never sent anywhere. '
                                     'No administrator rights are needed.'), muted=True))
            layout.addStretch()

    class Folder(QWizardPage):
        def __init__(self):
            super().__init__()
            self.setTitle(t('Папка установки', 'Install folder'))
            self.setSubTitle(t('Выберите, куда установить EGO.', 'Choose where to install EGO.'))
            layout = QVBoxLayout(self)
            row = QHBoxLayout()
            self.path = QLineEdit(str(core.default_dir()))
            self.path.textChanged.connect(self.completeChanged)
            browse = QPushButton(t('Обзор…', 'Browse…'))
            browse.clicked.connect(self.browse)
            row.addWidget(self.path, 1)
            row.addWidget(browse)
            layout.addLayout(row)
            self.note = label('', muted=True)
            layout.addWidget(self.note)
            layout.addStretch()
            self.registerField('folder*', self.path)

        def browse(self):
            chosen = QFileDialog.getExistingDirectory(self, t('Папка установки', 'Install folder'), self.path.text())
            if chosen:
                self.path.setText(str(Path(chosen) / 'EGO') if any(Path(chosen).iterdir()) and not (Path(chosen) / 'EGO.exe').exists() else chosen)

        def isComplete(self):
            folder = Path(self.path.text().strip() or '.')
            need = core.payload_size(core.payload_path()) + 300 * 1024 * 1024
            try:
                free = core.free_bytes(folder)
            except OSError:
                free = 0
            problem = core.check_target(folder) if self.path.text().strip() else 'EMPTY'
            if problem == 'EMPTY':
                self.note.setText('')
                return False
            if problem == 'NOT_WRITABLE':
                self.note.setText(t('В эту папку нельзя записывать. Выберите другую.', 'This folder is not writable. Choose another one.'))
                return False
            if problem == 'NOT_EGO_FOLDER':
                self.note.setText(t('Папка не пуста и в ней нет EGO. Выберите пустую папку или новую.', 'The folder is not empty and holds no EGO. Choose an empty or a new folder.'))
                return False
            self.note.setText(t(f'Нужно примерно {need / 1024 ** 2:.0f} МБ, свободно {free / 1024 ** 3:.1f} ГБ.',
                                f'About {need / 1024 ** 2:.0f} MB needed, {free / 1024 ** 3:.1f} GB free.'))
            return free > need

    class Components(QWizardPage):
        def __init__(self):
            super().__init__()
            self.setTitle(t('Компоненты', 'Components'))
            self.setSubTitle(t('EGO ставится всегда. Остальное можно доставить позже во вкладке «Зависимости».',
                               'EGO is always installed. The rest can be added later in the Dependencies tab.'))
            layout = QVBoxLayout(self)
            self.core_box = QCheckBox(t('EGO (программа и клиент)', 'EGO (the program and the client)'))
            self.core_box.setChecked(True)
            self.core_box.setEnabled(False)
            self.llama = QCheckBox(t('llama.cpp — запуск моделей на видеокарте NVIDIA', 'llama.cpp — runs models on an NVIDIA card'))
            self.llama.setChecked(True)
            self.cloud = QCheckBox(t('cloudflared — туннель для доступа из интернета', 'cloudflared — tunnel for access from the internet'))
            self.tts = QCheckBox(t('Озвучка (XTTS)', 'Voice output (XTTS)'))
            self.tts.setEnabled(False)
            self.python_note = label(t('Проверяю Python 3.12…', 'Checking for Python 3.12…'), muted=True)
            for box, detail in ((self.core_box, ''),
                                (self.llama, t('Скачивание, около 0,6 ГБ. Без него модель не запустится.', 'Download, about 0.6 GB. The model cannot run without it.')),
                                (self.cloud, t('Скачивание, около 40 МБ. По желанию.', 'Download, about 40 MB. Optional.')),
                                (self.tts, t('Нужен Python 3.12, около 6 ГБ. По желанию.', 'Needs Python 3.12, about 6 GB. Optional.'))):
                layout.addWidget(box)
                if detail:
                    note = label(detail, muted=True)
                    note.setContentsMargins(26, 0, 0, 6)
                    layout.addWidget(note)
            self.python_note.setContentsMargins(26, 0, 0, 6)
            layout.addWidget(self.python_note)
            layout.addWidget(label(t('Модели (файлы .gguf) в установщик не входят: скачайте их отдельно и положите в папку <b>models</b> внутри EGO, '
                                     'ссылки в README проекта.',
                                     'Models (.gguf files) are not included: download them separately and put them into the <b>models</b> folder '
                                     'inside EGO, see the project README.'), muted=True))
            self.desktop = QCheckBox(t('Ярлык на рабочем столе', 'Desktop shortcut'))
            self.desktop.setChecked(True)
            layout.addWidget(self.desktop)
            layout.addStretch()
            self.registerField('llama', self.llama)
            self.registerField('cloud', self.cloud)
            self.registerField('tts', self.tts)
            self.registerField('desktop', self.desktop)
            self._python = None
            threading.Thread(target=self._probe, daemon=True).start()
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._poll)
            self.timer.start(300)

        def _probe(self):
            self._python = core.find_python312() or []

        def _poll(self):
            if self._python is None:
                return
            self.timer.stop()
            if self._python:
                self.tts.setEnabled(True)
                self.python_note.setText(t('Python 3.12 найден.', 'Python 3.12 found.'))
            else:
                self.python_note.setText(t('Python 3.12 не найден: озвучка недоступна. Установите Python 3.12 (64 бит) и запустите установщик ещё раз.',
                                           'Python 3.12 not found: voice output is unavailable. Install Python 3.12 (64-bit) and run the setup again.'))

    class Install(QWizardPage):
        def __init__(self):
            super().__init__()
            self.setTitle(t('Установка', 'Installing'))
            self.setCommitPage(True)
            layout = QVBoxLayout(self)
            self.status = label(t('Нажмите «Установить».', 'Press Install.'))
            self.bar = QProgressBar()
            self.bar.setRange(0, 100)
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            self.log.setMaximumBlockCount(500)
            for widget in (self.status, self.bar, self.log):
                layout.addWidget(widget)
            self.finished_ok = False
            self.problems: list[str] = []
            self.started = False

        def initializePage(self):
            self.wizard().setButtonText(QWizard.WizardButton.CommitButton, t('Установить', 'Install'))
            self.wizard().setButtonText(QWizard.WizardButton.NextButton, t('Далее >', 'Next >'))

        def validatePage(self):
            if self.finished_ok:
                return True
            if self.started:
                return False
            self.started = True
            wizard_ = self.wizard()
            root = Path(wizard_.field('folder').strip())
            options = {'shortcuts': True, 'desktop': wizard_.field('desktop'), 'llama': wizard_.field('llama'),
                       'cloudflared': wizard_.field('cloud'), 'tts': wizard_.field('tts')}
            wizard_.button(QWizard.WizardButton.CommitButton).setEnabled(False)
            wizard_.button(QWizard.WizardButton.CancelButton).setEnabled(False)
            wizard_.button(QWizard.WizardButton.BackButton).setEnabled(False)
            self.worker = Worker(root, options)
            self.worker.progress.connect(self._progress)
            self.worker.line.connect(self.log.appendPlainText)
            self.worker.done.connect(self._done)
            self.worker.start()
            return False

        def _progress(self, value, text):
            self.bar.setValue(value)
            if text:
                self.status.setText(text)

        def _done(self, problems):
            self.problems = problems
            self.finished_ok = True
            self.bar.setValue(100)
            self.wizard().installed_root = Path(self.wizard().field('folder').strip())
            self.wizard().problems = problems
            self.wizard().next()

        def isComplete(self):
            return True

    class Finish(QWizardPage):
        def __init__(self):
            super().__init__()
            self.setTitle(t('Установка завершена', 'Setup complete'))
            layout = QVBoxLayout(self)
            self.text = label('')
            layout.addWidget(self.text)
            self.launch = QCheckBox(t('Запустить EGO', 'Start EGO'))
            self.launch.setChecked(True)
            layout.addStretch()
            layout.addWidget(self.launch)

        def initializePage(self):
            problems = getattr(self.wizard(), 'problems', [])
            lines = [t('EGO установлен. Окно сервера — ярлык «EGO» в меню «Пуск»; клиент — «EGO Client».',
                       'EGO is installed. The server window is the "EGO" shortcut in the Start menu; the client is "EGO Client".')]
            if problems:
                lines.append(t('Не удалось: ', 'Did not work: ') + ', '.join(problems) +
                             t('. Это можно поставить позже во вкладке «Зависимости» окна сервера.',
                               '. You can add it later in the Dependencies tab of the server window.'))
            lines.append(t('Не забудьте положить файл модели (.gguf) в папку models.', 'Remember to put a model file (.gguf) into the models folder.'))
            self.text.setText('<br><br>'.join(lines))
            self.wizard().setButtonText(QWizard.WizardButton.FinishButton, t('Готово', 'Finish'))

    wizard_ = QWizard()
    wizard_.setWindowTitle(t('Установка EGO', 'EGO setup'))
    wizard_.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
    wizard_.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
    wizard_.setOption(QWizard.WizardOption.NoCancelButtonOnLastPage, True)
    wizard_.setMinimumSize(640, 460)
    wizard_.setButtonText(QWizard.WizardButton.NextButton, t('Далее >', 'Next >'))
    wizard_.setButtonText(QWizard.WizardButton.BackButton, t('< Назад', '< Back'))
    wizard_.setButtonText(QWizard.WizardButton.CancelButton, t('Отмена', 'Cancel'))
    finish = Finish()
    for page in (Welcome(), Folder(), Components(), Install(), finish):
        wizard_.addPage(page)
    wizard_.installed_root = None
    wizard_.problems = []
    wizard_.show()
    code = app.exec()
    if wizard_.installed_root and finish.launch.isChecked() and wizard_.result() == QWizard.DialogCode.Accepted:
        exe = wizard_.installed_root / 'EGO.exe'
        if exe.exists():
            subprocess.Popen([str(exe)], cwd=str(wizard_.installed_root), creationflags=0x00000008 | 0x00000200)
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--silent', action='store_true')
    parser.add_argument('--dir')
    parser.add_argument('--llama', action='store_true')
    parser.add_argument('--cloudflared', action='store_true')
    parser.add_argument('--tts', action='store_true')
    parser.add_argument('--desktop', action='store_true')
    parser.add_argument('--no-shortcuts', action='store_true')
    args, _ = parser.parse_known_args(argv)
    if args.silent:
        return silent(args)
    return wizard()


if __name__ == '__main__':
    raise SystemExit(main())
