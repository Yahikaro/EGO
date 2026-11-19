"""Native presentation for the independent encrypted Core. No API keys live here."""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path
import time
from PySide6.QtCore import QTimer, QThreadPool, QUrl, Qt, QSettings, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon, QPalette, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QHBoxLayout, QVBoxLayout,
    QGridLayout, QFormLayout, QLabel, QPushButton, QListWidget, QStackedWidget,
    QScrollArea, QGroupBox, QLineEdit, QFileDialog, QSpinBox, QDoubleSpinBox,
    QComboBox, QCheckBox, QPlainTextEdit, QProgressBar, QMessageBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QSystemTrayIcon, QMenu, QStyle, QWizard, QWizardPage, QTextBrowser, QTabWidget, QSizePolicy, QToolButton, QGraphicsOpacityEffect, QAbstractItemView)
from windows_ui.configuration import ConfigurationManager
from windows_ui.backend import ControlBackend
from windows_ui.workers import Task
from windows_ui.theme import stylesheet
from server.ego_core import __version__



def _user_settings():
    """Per-Windows-user GUI preferences (registry). EGO_SETTINGS_DIR redirects
    them to an INI file so tests never read or overwrite the real user's keys."""
    override = os.environ.get('EGO_SETTINGS_DIR')
    if override:
        return QSettings(str(Path(override) / 'ServerControlCenter.ini'), QSettings.Format.IniFormat)
    return QSettings("EGO", "ServerControlCenter")

ERROR_HELP = {
 'CORE_INSTALLATION_MISMATCH': ('Порт отвечает другой установке EGO. Остановите её через её собственное окно или BAT; чужой процесс не завершается автоматически.', 'Another EGO installation owns this endpoint. Stop it using its own GUI or BAT; no foreign process is terminated automatically.'),
 'CORE_BUSY': ('Runtime занят генерацией или загрузкой. Дождитесь завершения либо отмените текущую генерацию.', 'Runtime is generating or loading. Wait for completion or cancel the current generation.'),
 'RUNTIME_NOT_READY': ('Модель ещё не готова или загрузка завершилась ошибкой. Проверьте состояние и диагностику; при ERROR выполните перезапуск Runtime.', 'The model is loading or failed. Check state and diagnostics; use Restart runtime after ERROR.'),
 'RUNTIME_EXITED': ('Процесс модели завершился. Проверьте код завершения, совместимость llama.cpp/CUDA, файлы модели и доступную VRAM. Повторный запуск выполняется явно.', 'The model process exited. Check its exit code, llama.cpp/CUDA compatibility, model files and available VRAM. Restart is explicit.'),
 'PORT_IN_USE': ('Порт занят. Остановите соответствующий процесс или выберите другой порт; не включайте совместное использование сокета.', 'Port is occupied. Stop its owner or choose another port; do not enable shared socket binding.'),
 'CORE_PORT_IN_USE': ('API-порт занят. Проверьте владельца listener в диагностике.', 'The API port is occupied. Inspect its listener owner in diagnostics.'),
 'MODEL_NOT_PROVIDED': ('Выберите существующий GGUF-файл во вкладке «Запуск».', 'Choose an existing GGUF file in the Launch tab.'),
 'RUNTIME_NOT_PROVIDED': ('Выберите существующий llama-server.exe во вкладке «Запуск».', 'Choose an existing llama-server binary in the Launch tab.'),
 'GPU_OFFLOAD_NOT_VERIFIED': ('GPU-ускорение пока не подтверждено. Наличие NVIDIA или CUDA-сборки не подтверждает offload.', 'GPU acceleration is not verified. A detected NVIDIA device or CUDA build does not prove offload.'),
 'CORE_LOCKED': ('Хранилище недоступно. Используйте исходный профиль Windows; не удаляйте ключи и базы.', 'Storage is locked. Use the original Windows user; do not delete keys or databases.'),
 'DPAPI_UNAVAILABLE': ('DPAPI требует Windows и исходный профиль пользователя. Обычный SQLite не используется.', 'DPAPI requires Windows and the original user profile. Plain SQLite is not used.'),
 'SQLCIPHER_UNAVAILABLE': ('Установка SQLCipher неполна. Повторите SETUP; импорт SQLite не подтверждает шифрование.', 'SQLCipher installation is incomplete. Run SETUP; an SQLite import does not prove encryption.'),
 'CONFIG_INVALID': ('Исправьте настройки во вкладке «Запуск». Существующие данные не сбрасываются.', 'Repair configuration in the Launch tab. Existing data is preserved.'),
 'UNSAVED_CHANGES': ('Сначала сохраните изменения профиля.', 'Save profile changes first.'),
 'UNSUPPORTED_RUNTIME_OPTION': ('Эта сборка llama.cpp не поддерживает выбранный параметр. Верните Auto или выберите совместимую сборку.', 'This llama.cpp build does not support a selected option. Use Auto or choose a compatible build.'),
}
LANGUAGES = [('ru','Русский'),('en','English'),('fr','Français'),('es','Español'),('de','Deutsch')]
NAV = [('Общее','General'),('Запуск','Launch'),('Здоровье','Health'),('Доступ','Access'),('Настройки','Settings'),('Зависимости','Dependencies')]
ACTIONS = [('smoke','Пробный ответ модели','Trial reply'),('benchmark','Замер скорости модели','Model speed test'),('environment','Проверка компьютера (Python, Windows, GPU)','Computer check (Python, Windows, GPU)'),('full_tests','Все автотесты (для разработчиков)','All automated tests (developers)')]


class _TabTitle:
    """Lets the translation list retitle one tab of a QTabWidget through setText."""
    def __init__(self, tabs, index): self.tabs, self.index = tabs, index
    def setText(self, text): self.tabs.setTabText(self.index, text)


class _SubLayout(QVBoxLayout):
    """A page layout inside a shared tab: the tab itself keeps the closing stretch."""
    def addStretch(self, *args, **kwargs): pass


class ControlCenterWindow(QMainWindow):
    def __init__(self, root: Path, backend=None):
        super().__init__()
        self.root = Path(root).absolute()
        self.backend = backend or ControlBackend(self.root)
        self.configuration = ConfigurationManager(self.root)
        # Shared per-Windows-user paths survive unpacking new EGO releases.
        # Profiles and Core configuration remain authoritative for other options.
        self.path_preferences = _user_settings()
        self.snapshot = self.configuration.snapshot()
        self.lang = self.snapshot['gui']['language']
        self._translations = []
        self._tasks = set()
        self._action = None
        self._allow_close = False
        self._stop_close = False
        self._online = False
        self._polling = False
        self._dirty = False
        self._loading = False
        self._active_profile = None
        self.busy = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.setWindowTitle(f'EGO · Control Center · {__version__}')
        self.setMinimumSize(760, 480)
        self.resize(1220, 830)
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        shell = QWidget(); outer = QHBoxLayout(shell); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        sidebar = QWidget(); sidebar.setObjectName('sidebar'); sidebar.setMinimumWidth(190); sidebar.setMaximumWidth(230)
        side = QVBoxLayout(sidebar); side.setContentsMargins(16,24,16,18)
        brand = QLabel('EGO'); brand.setObjectName('brand'); side.addWidget(brand)
        side.addWidget(self.label('Сервер','Server',muted=True)); version=QLabel(__version__); version.setObjectName('muted'); side.addWidget(version)
        self.navigation = QListWidget(); self.navigation.setObjectName('navigation'); side.addWidget(self.navigation,1)
        self.connection_label = QLabel('API · —'); self.connection_label.setWordWrap(True); side.addWidget(self.connection_label)
        side.addWidget(self.label('Всё работает на этом ПК','Everything runs on this PC',muted=True))
        outer.addWidget(sidebar)
        self.pages = QStackedWidget(); outer.addWidget(self.pages,1)
        self.setCentralWidget(shell)
        self.statusBar().showMessage(self.t('Готово','Ready'))
        self.action_buttons = []
        self._tab_inner=None; self._tab_layout=None
        self._begin_tab('Общее','General'); self._dashboard(); self._end_tab()
        self._begin_tab('Запуск','Launch'); self._models(); self._end_tab()
        self._begin_tab('Здоровье','Health',inner=True); self._monitoring(); self._testing(); self._logs(); self._playground()
        self._begin_tab('Доступ','Access',inner=True); self._accounts(); self._access_network(); self._access_tunnel()
        self._begin_tab('Настройки','Settings'); self._settings(); self._end_tab()
        self._begin_tab('Зависимости','Dependencies'); self._dependencies(); self._end_tab()
        self.navigation.currentRowChanged.connect(self._animated_navigation)
        self._retranslate(); self.navigation.setCurrentRow(0)
        self.reload_profiles()
        self._tray()
        self.setStyleSheet(stylesheet(self.snapshot['gui']['theme']))
        self.poll_timer = QTimer(self); self.poll_timer.setInterval(2500); self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start()
        self.elapsed_timer = QTimer(self); self.elapsed_timer.setInterval(250); self.elapsed_timer.timeout.connect(self._elapsed)
        QTimer.singleShot(0,self.poll)
        if backend is None:
            QTimer.singleShot(0,lambda:self.run_action('auto_start'))
        if self.snapshot['error']: self._show_error(self.snapshot['error'])
        if backend is None and not self.snapshot['profiles']:
            QTimer.singleShot(150,self.show_setup)

    def _animated_navigation(self, index):
        if index < 0: return
        self.pages.setCurrentIndex(index)
        page=self.pages.currentWidget()
        if page is None: return
        # Fade only page widgets; no animation touches the backend or chat state.
        effect=QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        animation=QPropertyAnimation(effect,b'opacity',page)
        animation.setDuration(155)
        animation.setStartValue(0.72)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        def cleanup():
            if page.graphicsEffect() is effect: page.setGraphicsEffect(None)
        animation.finished.connect(cleanup)
        page._fade_animation=animation
        animation.start()

    def t(self,ru,en):
        from windows_ui.translations import translate
        return translate(self.lang,ru,en)

    def label(self,ru,en,muted=False):
        widget = QLabel(self.t(ru,en)); widget.setWordWrap(True)
        if muted: widget.setObjectName('muted')
        self._translations.append((widget,'setText',ru,en)); return widget

    def button(self,ru,en,callback,primary=False,action=False):
        widget = QPushButton(self.t(ru,en)); widget.clicked.connect(callback)
        if primary: widget.setObjectName('primary')
        self._translations.append((widget,'setText',ru,en))
        if action: self.action_buttons.append(widget)
        return widget

    def group(self,ru,en):
        widget=QGroupBox(self.t(ru,en)); self._translations.append((widget,'setTitle',ru,en)); return widget

    def _begin_tab(self,ru,en,inner=False):
        """Start one top-level tab. inner=True: a tab with sub-tabs, every page() becomes a sub-tab."""
        self._tab_inner=None; self._tab_layout=None
        if inner:
            tabs=QTabWidget(); self.pages.addWidget(tabs); self._tab_inner=tabs; return
        content=QWidget(); layout=QVBoxLayout(content); layout.setContentsMargins(26,22,26,22); layout.setSpacing(16)
        heading=self.label(ru,en); heading.setObjectName('heading'); layout.addWidget(heading)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(content); self.pages.addWidget(scroll)
        self._tab_layout=layout

    def _end_tab(self):
        if self._tab_layout is not None: self._tab_layout.addStretch()

    def page(self,ru,en):
        """A page of the current tab: a sub-tab (inner tabs) or a section of the tab."""
        if self._tab_inner is not None:
            content=QWidget(); layout=QVBoxLayout(content); layout.setContentsMargins(22,18,22,18); layout.setSpacing(16)
            scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(content)
            index=self._tab_inner.addTab(scroll,self.t(ru,en)); self._translations.append((_TabTitle(self._tab_inner,index),'setText',ru,en))
            return layout
        sub=_SubLayout(); sub.setSpacing(16); self._tab_layout.addLayout(sub); return sub

    def card(self,ru,en):
        frame=QFrame(); frame.setObjectName('card'); layout=QVBoxLayout(frame); layout.setContentsMargins(18,16,18,16)
        layout.addWidget(self.label(ru,en,muted=True)); value=QLabel('—'); value.setObjectName('value'); value.setWordWrap(True); layout.addWidget(value)
        return frame,value

    def _dashboard(self):
        page=self.page('Центр управления','Control center')
        page.addWidget(self.label('Модели и истории остаются на этом компьютере. Сервер продолжает работать, даже если закрыть это окно.','Models and stories stay on this computer. The server keeps running when this window is closed.',muted=True))
        grid=QGridLayout(); self.dashboard_values={}
        for index,(key,ru,en) in enumerate([('core','Сервер','Server'),('runtime','Модель','Model state'),('model','Файл модели','Model file'),('cpu','Процессор','CPU'),('ram','Оперативная память','RAM'),('gpu','GPU / VRAM','GPU / VRAM')]):
            card,value=self.card(ru,en); grid.addWidget(card,index//3,index%3); self.dashboard_values[key]=value
        page.addLayout(grid)
        row=QHBoxLayout()
        for action,ru,en in [('start','Запустить','Start'),('stop','Остановить','Stop'),('restart','Перезапустить сервер','Restart server'),('runtime_restart','Перезагрузить модель','Reload model'),('smoke','Пробный ответ','Trial reply')]:
            button=self.button(ru,en,lambda checked=False,a=action:self.run_action(a),primary=action=='start',action=True)
            if action=='runtime_restart': button.setProperty('runtime_restart',True)
            row.addWidget(button)
        page.addLayout(row)
        self.uptime_label=QLabel('Uptime · —'); page.addWidget(self.uptime_label)
        self.error_label=QLabel('—'); self.error_label.setWordWrap(True); self.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); page.addWidget(self.error_label)
        page.addWidget(self.button('Мастер первоначальной настройки','Setup wizard',self.show_setup))
        page.addWidget(self.label('Истории зашифрованы и открываются только под вашей учётной записью Windows.','Stories are encrypted and open only under your Windows account.',muted=True))
        page.addStretch()

    def _path_row(self,field,model=False):
        row=QWidget(); layout=QHBoxLayout(row); layout.setContentsMargins(0,0,0,0); layout.addWidget(field,1)
        layout.addWidget(self.button('Выбрать…','Browse…',lambda:self.browse_path(field,model)))
        return row

    def _remember_model_paths(self):
        """Persist only valid file locations, never keys or private chat data."""
        for key in ('binary', 'model'):
            value = self.model_fields[key].text().strip()
            if value and Path(value).is_file():
                self.path_preferences.setValue('model_paths/' + key, value)
        self.path_preferences.sync()

    def _model_folders(self):
        raw = self.path_preferences.value('model_paths/folders', '', type=str) or ''
        return [line for line in raw.split('\n') if line.strip()]

    def _preferred_model_path(self, key, existing):
        # Never overwrite a path from the selected profile, even a missing one:
        # it stays visible so the user can repair it.
        if isinstance(existing, str) and existing.strip():
            return existing
        saved = self.path_preferences.value('model_paths/' + key, '', type=str)
        if isinstance(saved, str) and saved and Path(saved).is_file():
            return saved
        if key == 'binary':
            from windows_ui.model_library import find_llama_server
            return find_llama_server(self.root) or existing
        return existing

    def browse_path(self,field,model=False):
        path,_=QFileDialog.getOpenFileName(self,self.t('Выберите файл','Choose file'),field.text(), 'GGUF (*.gguf);;All files (*)' if model else 'Executable (*.exe);;All files (*)')
        if path:
            field.setText(path)
            self._remember_model_paths()

    # ---- model list (2026-11-06): models found on this PC + any file by path ----
    def _rescan_models(self):
        from windows_ui.model_library import find_models
        known=[p['values'].get('model') for p in self.snapshot.get('profiles',[])]
        known+=[self.snapshot.get('core',{}).get('model'),self.path_preferences.value('model_paths/model','',type=str),
                self.model_fields['model'].text() if 'model' in getattr(self,'model_fields',{}) else '']
        try: self._model_rows=find_models(self.root,known,self._model_folders())
        except Exception: self._model_rows=[]
        if hasattr(self,'model_choice'): self._sync_model_choice()

    def _sync_model_choice(self):
        from windows_ui.model_library import size_text
        path=self.model_fields['model'].text().strip()
        self.model_choice.blockSignals(True); self.model_choice.clear()
        self.model_choice.addItem(self.t('— выберите модель —','— choose a model —'),None)
        selected=0
        for row in self._model_rows:
            self.model_choice.addItem(f"{row['name']}  ·  {size_text(row['size'],self.lang)}  ·  {Path(row['path']).parent}",row['path'])
            if path and row['path'].casefold()==path.casefold(): selected=self.model_choice.count()-1
        if path and not selected:
            self.model_choice.addItem(Path(path).name+self.t('  ·  файл не найден','  ·  file not found'),path)
            selected=self.model_choice.count()-1
        self.model_choice.setCurrentIndex(selected); self.model_choice.blockSignals(False)
        self._show_model_size(path)

    def _show_model_size(self,path):
        try: size=Path(path).stat().st_size if path and Path(path).is_file() else None
        except OSError: size=None
        if not path: text=self.t('Модель не выбрана.','No model chosen.')
        elif size is None: text=self.t('Файл модели не найден: ','Model file not found: ')+path
        else: text=self.t('Файл: ','File: ')+path
        self.model_size.setText(text)

    def _model_chosen(self,*_):
        path=self.model_choice.currentData()
        if path and path!=self.model_fields['model'].text():
            self.model_fields['model'].setText(path)

    def _browse_model(self):
        start=self.model_fields['model'].text() or (self._model_folders() or [''])[0]
        path,_=QFileDialog.getOpenFileName(self,self.t('Выберите файл модели','Choose a model file'),start,'GGUF (*.gguf);;All files (*)')
        if not path: return
        folder=str(Path(path).parent); folders=self._model_folders()
        if folder.casefold() not in {f.casefold() for f in folders}:
            self.path_preferences.setValue('model_paths/folders','\n'.join([folder,*folders][:20]))
        self.model_fields['model'].setText(path); self._remember_model_paths(); self._rescan_models()

    def _models(self):
        page=self.page('Запуск','Launch')
        page.addWidget(self.label('Выберите готовую модель или свой профиль. «Сохранить» запоминает настройки, «Применить» загружает модель.',
                                  'Pick a ready-made model or your own profile. Save stores the settings, Apply loads the model.',muted=True))
        row=QHBoxLayout(); self.profile_combo=QComboBox(); self.profile_combo.setMinimumContentsLength(15); self.profile_combo.currentIndexChanged.connect(self._profile_selected); row.addWidget(self.profile_combo,1)
        row.addWidget(self.button('Новый','New',self.new_profile)); row.addWidget(self.button('Убрать из списка','Remove from list',self.remove_profile)); page.addLayout(row)
        self.profile_info=QLabel(''); self.profile_info.setWordWrap(True); self.profile_info.setObjectName('presetInfo')
        self.profile_info.setOpenExternalLinks(True); self.profile_info.setTextFormat(Qt.TextFormat.RichText); page.addWidget(self.profile_info)
        self.model_fields={}
        form=QFormLayout()
        self.profile_name=QLineEdit(); self.profile_name.setMaxLength(128); form.addRow(self.label('Название','Name'),self.profile_name)
        # The visible chooser lists models found on this PC; the hidden field holds the exact path.
        model_field=QLineEdit(); model_field.setMaxLength(4096); model_field.setObjectName('model'); model_field.hide(); self.model_fields['model']=model_field
        self.model_choice=QComboBox(); self.model_choice.setObjectName('modelChoice'); self.model_choice.setMinimumContentsLength(20)
        self.model_choice.currentIndexChanged.connect(self._model_chosen)
        model_row=QWidget(); ml=QHBoxLayout(model_row); ml.setContentsMargins(0,0,0,0); ml.addWidget(self.model_choice,1)
        ml.addWidget(self.button('Выбрать файл…','Choose file…',self._browse_model)); ml.addWidget(self.button('Обновить','Rescan',self._rescan_models))
        form.addRow(self.label('Модель (GGUF)','Model (GGUF)'),model_row)
        self.model_size=QLabel('—'); self.model_size.setObjectName('muted'); self.model_size.setWordWrap(True); form.addRow(QLabel(''),self.model_size)
        binary=QLineEdit(); binary.setMaxLength(4096); binary.setObjectName('binary'); self.model_fields['binary']=binary
        form.addRow(self.label('llama-server.exe','llama-server.exe'),self._path_row(binary))
        # Measured modes fill only tuning fields; paths and ports stay the profile's own.
        from server.ego_core.presets import PRESETS, DEFAULT_PRESET_ID
        mode=QWidget(); mr=QHBoxLayout(mode); mr.setContentsMargins(0,0,0,0)
        self.preset_combo=QComboBox(); self.preset_combo.setObjectName('preset')
        for item in PRESETS: self.preset_combo.addItem(self.t(*item['name']),item['id'])
        self.preset_combo.setCurrentIndex(max(0,self.preset_combo.findData(DEFAULT_PRESET_ID)))
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        mr.addWidget(self.preset_combo,1); mr.addWidget(self.button('Подставить','Use mode',self.fill_from_preset))
        form.addRow(self.label('Режим','Mode'),mode)
        page.addLayout(form)
        self.preset_info=QLabel(''); self.preset_info.setWordWrap(True); self.preset_info.setObjectName('muted'); page.addWidget(self.preset_info)
        self._preset_changed()
        generation=self.group('Ответы модели','Replies'); gf=QFormLayout(generation)
        for key,ru,en,minimum,maximum,default in [('temperature','Температура (выше — разнообразнее)','Temperature (higher = more varied)',0.,10.,0.),('top_p','Top-P','Top-P',.01,1.,1.),('min_p','Min-P','Min-P',0.,1.,0.),('repeat_penalty','Штраф за повторы','Repeat penalty',.01,100.,1.)]:
            field=QDoubleSpinBox(); field.setRange(minimum,maximum); field.setDecimals(2); field.setSingleStep(.01 if key=='min_p' else .05); field.setValue(default); self.model_fields[key]=field; gf.addRow(self.label(ru,en),field)
        for key,ru,en,minimum,maximum,default in [('top_k','Top-K','Top-K',0,100000,40),('max_tokens','Длина ответа, токенов','Max reply tokens',1,4096,128)]:
            field=QSpinBox(); field.setRange(minimum,maximum); field.setValue(default); self.model_fields[key]=field; gf.addRow(self.label(ru,en),field)
        loading=self.group('Загрузка модели','Model loading'); lf=QFormLayout(loading)
        for key,ru,en,minimum,maximum,default in [('context','Контекст, токенов','Context, tokens',256,32768,2048),('gpu_layers','Слоёв на видеокарте','GPU layers',0,999,99)]:
            field=QSpinBox(); field.setRange(minimum,maximum); field.setValue(default); field.setObjectName(key); self.model_fields[key]=field; lf.addRow(self.label(ru,en),field)
        flash=QComboBox(); flash.addItems(['Auto','On','Off']); self.model_fields['flash_attention']=flash; lf.addRow(QLabel('Flash Attention'),flash)
        groups=QHBoxLayout(); groups.addWidget(generation,1); groups.addWidget(loading,1); page.addLayout(groups)
        row=QHBoxLayout()
        row.addWidget(self.button('Сохранить','Save',self.save_profile,primary=True))
        row.addWidget(self.button('Применить','Apply',self.apply_profile,action=True))
        row.addWidget(self.button('Отменить изменения','Undo changes',self.reload_profiles)); row.addStretch(); page.addLayout(row)
        self.model_notice=QLabel(''); self.model_notice.setWordWrap(True); page.addWidget(self.model_notice)
        advanced,al,self.models_advanced_toggle=self.collapsible(self.t('Дополнительно: порты, процессор, проверка, импорт','More: ports, CPU, checks, import'),False)
        af=QFormLayout()
        for key,ru,en,minimum,maximum,default in [('threads','Потоки процессора (0 = авто)','CPU threads (0 = auto)',0,1024,0),('batch_size','Batch (0 = авто)','Batch (0 = auto)',0,65536,0),('runtime_port','Порт модели','Model port',1024,65535,18081),('api_port','Порт API','API port',1024,65535,18443)]:
            field=QSpinBox(); field.setRange(minimum,maximum); field.setValue(default); field.setObjectName(key); self.model_fields[key]=field; af.addRow(self.label(ru,en),field)
        startup=QDoubleSpinBox(); startup.setRange(.1,600); startup.setValue(120); startup.setSuffix(' s'); self.model_fields['startup_timeout']=startup; af.addRow(self.label('Время на загрузку модели','Model loading timeout'),startup)
        al.addLayout(af)
        row=QHBoxLayout()
        row.addWidget(self.button('Проверить файлы','Check files',self.validate_model,action=True))
        row.addWidget(self.button('Последняя рабочая','Last working',self.restore_working,action=True))
        row.addWidget(self.button('Импорт профиля','Import profile',self.import_profile)); row.addWidget(self.button('Экспорт профиля','Export profile',self.export_profile)); al.addLayout(row)
        row=QHBoxLayout()
        row.addWidget(self.button('Что загружено сейчас','What is loaded now',self.check_runtime_model))
        self.runtime_api_info=QLabel(''); self.runtime_api_info.setWordWrap(True)
        self.runtime_api_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.runtime_api_info,1); al.addLayout(row)
        page.addWidget(advanced); page.addStretch()
        self._model_rows=[]
        model_field.textChanged.connect(lambda *_: self._sync_model_choice())
        for key in ("binary", "model"):
            self.model_fields[key].editingFinished.connect(self._remember_model_paths)
        self.profile_name.textChanged.connect(self._changed)
        for field in self.model_fields.values():
            if isinstance(field,QLineEdit): field.textChanged.connect(self._changed)
            elif isinstance(field,QComboBox): field.currentIndexChanged.connect(self._changed)
            else: field.valueChanged.connect(self._changed)

    def _monitoring(self):
        page=self.page('Мониторинг','Monitoring')
        page.addWidget(self.label('Обновляется каждые несколько секунд. Если показатель недоступен, стоит «Неизвестно».','Updates every few seconds. Unavailable values show Unknown.',muted=True))
        grid=QGridLayout(); self.monitor_values={}
        for i,(key,ru,en) in enumerate([('cpu_percent','CPU','CPU'),('ram','RAM','RAM'),('gpu_percent','Загрузка GPU','GPU load'),('vram','VRAM','VRAM'),('gpu_temperature_c','Температура GPU','GPU temperature'),('model_load_seconds','Загрузка модели, сек','Model load, seconds'),('tokens_per_second','Токенов / сек','Tokens / second'),('context_used','Использование контекста','Context used'),('runtime_listener','Процесс Runtime','Runtime process'),('prompt_tokens','Входных токенов','Prompt tokens'),('completion_tokens','Выходных токенов','Completion tokens'),('generation_seconds','Время генерации, сек','Generation seconds'),('finish_reason','Завершение ответа','Finish reason'),('queued_generations','В очереди','Queued requests')]):
            frame,value=self.card(ru,en); self.monitor_values[key]=value; grid.addWidget(frame,i//3,i%3)
        page.addLayout(grid); self.monitor_notice=QLabel(''); self.monitor_notice.setWordWrap(True); page.addWidget(self.monitor_notice); page.addStretch()

    def _testing(self):
        page=self.page('Проверки','Checks')
        self.test_combo=QComboBox(); page.addWidget(self.test_combo)
        row=QHBoxLayout(); row.addWidget(self.button('Запустить проверку','Run check',lambda:self.run_action(self.test_combo.currentData()),primary=True,action=True))
        self.cancel_button=self.button('Отменить задачу','Cancel task',self.cancel_task); self.cancel_button.setEnabled(False); row.addWidget(self.cancel_button); page.addLayout(row)
        self.stage_label=QLabel('—'); page.addWidget(self.stage_label)
        self.progress_bar=QProgressBar(); self.progress_bar.setRange(0,1); self.progress_bar.setValue(0); self.progress_bar.setTextVisible(False); page.addWidget(self.progress_bar)
        self.result_label=QLabel('—'); self.result_label.setWordWrap(True); page.addWidget(self.result_label)
        self.result_text=QPlainTextEdit(); self.result_text.setReadOnly(True); self.result_text.setMaximumBlockCount(1000); self.result_text.setMinimumHeight(160); page.addWidget(self.result_text,1)
        self.history_table=QTableWidget(0,4); self.history_table.setHorizontalHeaderLabels(['UTC','Action','Status','Seconds']); self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); self.history_table.setMinimumHeight(150); page.addWidget(self.history_table)
        page.addWidget(self.button('Открыть папку результатов','Open results folder',lambda:self.open_local(self.root/'logs')))
        self.refresh_history()

    def _logs(self):
        page=self.page('Логи и диагностика','Logs & diagnostics')
        row=QHBoxLayout(); self.log_level=QComboBox(); self.log_level.addItems(['ALL','INFO','WARNING','ERROR']); row.addWidget(self.log_level)
        self.log_search=QLineEdit(); self.log_search.setMaxLength(200); row.addWidget(self.log_search,1)
        row.addWidget(self.button('Обновить','Refresh',self.refresh_logs)); page.addLayout(row)
        self.log_text=QPlainTextEdit(); self.log_text.setReadOnly(True); self.log_text.setMaximumBlockCount(2500); self.log_text.setMinimumHeight(240); page.addWidget(self.log_text,1)
        row=QHBoxLayout(); row.addWidget(self.button('Копировать','Copy',lambda:QApplication.clipboard().setText(self.log_text.textCursor().selectedText() or self.log_text.toPlainText())))
        row.addWidget(self.button('Папка логов','Log folder',lambda:self.open_local(self.root/'logs')))
        row.addWidget(self.button('Экспорт диагностики ZIP','Export diagnostics ZIP',self.export_diagnostics,action=True)); row.addWidget(self.button('Удалить логи старше 30 дней','Clear logs older than 30 days',self.clean_logs,action=True)); page.addLayout(row)
        helpbox=self.group('Частые ошибки','Troubleshooting'); self.help_text=QLabel(); self.help_text.setWordWrap(True); QVBoxLayout(helpbox).addWidget(self.help_text); page.addWidget(helpbox)
        page.addWidget(self.label('Показаны только разрешённые диагностические поля. Ключи, переписка, токены и произвольные файлы не включаются в экспорт.','Only approved diagnostic fields are displayed. Keys, conversations, tokens and arbitrary files are excluded from exports.',muted=True))

    def collapsible(self, title: str, initially_open=False):
        """Compact animated disclosure; never destroys the contents on collapse."""
        shell=QWidget(); layout=QVBoxLayout(shell); layout.setContentsMargins(0,0,0,0); layout.setSpacing(4)
        toggle=QToolButton(); toggle.setObjectName('disclosure')
        toggle.setText(title); toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.ArrowType.DownArrow if initially_open else Qt.ArrowType.RightArrow)
        toggle.setCheckable(True); toggle.setChecked(initially_open)
        layout.addWidget(toggle)
        body=QWidget(); inside=QVBoxLayout(body); inside.setContentsMargins(10,8,10,8); inside.setSpacing(8)
        body.setVisible(initially_open); layout.addWidget(body)
        animation=QPropertyAnimation(body,b'maximumHeight',shell)
        animation.setDuration(180); animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        def change(opened):
            toggle.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)
            animation.stop()
            if opened:
                body.setVisible(True)
                body.setMaximumHeight(16777215)
                h=body.sizeHint().height()
                body.setMaximumHeight(0)
                animation.setStartValue(0); animation.setEndValue(max(h,1)); animation.start()
            else:
                animation.setStartValue(body.height()); animation.setEndValue(0); animation.start()
        def finish():
            if toggle.isChecked(): body.setMaximumHeight(16777215)
            else: body.setVisible(False)
        toggle.toggled.connect(change); animation.finished.connect(finish)
        shell._collapse_animation=animation
        return shell,inside,toggle

    def _playground(self):
        page=self.page('Песочница','Playground')
        page.addWidget(self.label(
            'Быстрая проверка модели без клиента. Диалоги хранятся на сервере в зашифрованном виде и видны в клиенте.',
            'A quick model check without the client. Chats are stored encrypted on the server and appear in the client.', muted=True))
        character_box,cl,self.play_character_toggle=self.collapsible(self.t('Персонаж','Character'),False)
        self.play_character_name=QLineEdit(); self.play_character_name.setMaxLength(120)
        self.play_character_name.setPlaceholderText(self.t('Имя персонажа (необязательно)','Character name (optional)'))
        cl.addWidget(self.play_character_name)
        self.play_character_description=QPlainTextEdit(); self.play_character_description.setMaximumHeight(110)
        self.play_character_description.setPlaceholderText(self.t('Характер, стиль речи, правила управления ролью (до 4000 символов).','Personality, speech style, role rules (up to 4000 characters).'))
        cl.addWidget(self.play_character_description)
        self.play_appearance=QPlainTextEdit(); self.play_appearance.setMaximumHeight(65)
        self.play_appearance.setPlaceholderText(self.t('Внешность персонажа (необязательно)','Character appearance (optional)'))
        cl.addWidget(self.play_appearance)
        self.play_speech=QPlainTextEdit(); self.play_speech.setMaximumHeight(65)
        self.play_speech.setPlaceholderText(self.t('Манера речи (необязательно)','Manner of speech (optional)'))
        cl.addWidget(self.play_speech)
        self.play_scenario=QPlainTextEdit(); self.play_scenario.setMaximumHeight(75)
        self.play_scenario.setPlaceholderText(self.t('Стартовая сцена (необязательно)','Opening scene (optional)'))
        cl.addWidget(self.play_scenario)
        cl.addWidget(self.label('Применяется к следующей генерации. Для старого чата заполняйте те же поля снова. Не сохраняется в памяти персонажа.', 'Used for the next generation only; not saved into character memory.', muted=True))
        card_row=QHBoxLayout()
        self.play_card_selector=QComboBox(); self.play_card_selector.addItem(self.t('Карточки персонажей (выбрать)','Character cards (choose)'),None)
        card_row.addWidget(self.play_card_selector,1)
        card_row.addWidget(self.button('Сохранить карточку','Save card',self._card_save))
        card_row.addWidget(self.button('Удалить карточку','Delete card',self._card_delete))
        cl.addLayout(card_row)
        cl.addWidget(self.button('Предпросмотр инструкций','Preview instructions',self._play_prompt_preview))
        self.play_card_selector.currentIndexChanged.connect(self._card_load)
        self._card_refresh()
        page.addWidget(character_box)
        row=QHBoxLayout()
        self.play_chats=QComboBox(); self.play_chats.setMinimumWidth(210)
        row.addWidget(self.play_chats,1)
        row.addWidget(self.button('Обновить чаты','Refresh chats',self._play_refresh))
        row.addWidget(self.button('Новый диалог','New chat',self._play_new_chat))
        page.addLayout(row)
        self.play_history=QTextBrowser(); self.play_history.setOpenExternalLinks(False); self.play_history.setReadOnly(True)
        self.play_history.setPlaceholderText(self.t('История сообщений появится здесь.','Message history appears here.'))
        self.play_history.setMinimumHeight(210); page.addWidget(self.play_history,1)
        self.play_prompt=QPlainTextEdit()
        self.play_prompt.setPlaceholderText(self.t('Напишите следующее сообщение...','Write the next message...'))
        self.play_prompt.setMaximumHeight(110); page.addWidget(self.play_prompt)
        row=QHBoxLayout()
        self.play_send=self.button('Отправить','Send',self._play_start,primary=True)
        self.play_stop=self.button('Остановить','Stop',self._play_stop); self.play_stop.setEnabled(False)
        self.play_regen=self.button('Перегенерировать','Regenerate',self._play_regenerate)
        self.play_continue=self.button('Продолжить','Continue',self._play_continue)
        row.addWidget(self.play_send); row.addWidget(self.play_regen); row.addWidget(self.play_continue); row.addWidget(self.play_stop); page.addLayout(row)
        self.play_state=QLabel('—'); page.addWidget(self.play_state)
        technical,tech_layout,self.play_answer_toggle=self.collapsible(self.t('Текст ответа без оформления','Raw reply text'),False)
        self.play_answer=QPlainTextEdit(); self.play_answer.setReadOnly(True)
        self.play_answer.setPlaceholderText(self.t('Текущий ответ модели...','Current model response...'))
        self.play_answer.setMinimumHeight(95); self.play_answer.setMaximumHeight(230); tech_layout.addWidget(self.play_answer)
        page.addWidget(technical)
        self._play_task=None; self._play_started=0; self._play_chat_id=None; self._play_messages=[]; self._play_pending_prompt=None
        self.play_chats.currentIndexChanged.connect(self._play_select)
        QTimer.singleShot(2500,self._play_refresh)

    # Character cards live in the encrypted EGO Core (decision D2), shared with the
    # EGO client. The old registry copy is migrated once and then removed.
    def _card_refresh(self, selected=None):
        from windows_ui import core_characters as cards
        cards_raw = self.path_preferences.value(cards.REGISTRY_CARDS, '')
        profiles_raw = self.path_preferences.value(cards.REGISTRY_PROFILES, '')
        def load(progress, cancel):
            migrated = 0
            if cards_raw:
                migrated = cards.migrate_registry(self.root, cards_raw, profiles_raw)
            return {'status': 'PASS', 'records': cards.list_cards(self.root), 'migrated': migrated,
                    'registry': bool(cards_raw)}
        def done(result):
            if result.get('status') != 'PASS':
                self._card_records = {}
                self._card_fill(None)
                self.play_state.setText(self.t('Карточки персонажей хранятся в EGO Core — запустите Core, чтобы их открыть.',
                                               'Character cards are stored in EGO Core — start Core to open them.'))
                return
            if result['registry']:
                # Verified copy is in Core: drop the plaintext registry values.
                self.path_preferences.remove(cards.REGISTRY_CARDS)
                self.path_preferences.remove(cards.REGISTRY_PROFILES)
                self.path_preferences.sync()
                if result['migrated']:
                    self.play_state.setText(self.t(f"Карточки из реестра перенесены в EGO Core: {result['migrated']}.",
                                                   f"Registry cards moved to EGO Core: {result['migrated']}."))
            self._card_records = {r['id']: r for r in result['records']}
            self._card_fill(selected)
        self._card_records = getattr(self, '_card_records', {})
        self._submit(load, done)

    def _card_fill(self, selected):
        self.play_card_selector.blockSignals(True)
        self.play_card_selector.clear()
        self.play_card_selector.addItem(self.t('Карточки персонажей (выбрать)','Character cards (choose)'),None)
        for record in sorted(self._card_records.values(), key=lambda r: str(r['data'].get('name', '')).casefold()):
            self.play_card_selector.addItem(str(record['data'].get('name', record['id'])), record['id'])
        if selected is not None:
            index=self.play_card_selector.findData(selected)
            if index>=0: self.play_card_selector.setCurrentIndex(index)
        self.play_card_selector.blockSignals(False)

    def _card_load(self,*_):
        from windows_ui.core_characters import to_form
        record=self._card_records.get(self.play_card_selector.currentData())
        if record:
            form=to_form(record['data'])
            self.play_character_name.setText(str(record['data'].get('name','')))
            self.play_character_description.setPlainText(form['personality'])
            self.play_appearance.setPlainText(form['appearance'])
            self.play_speech.setPlainText(form['speech'])
            self.play_scenario.setPlainText(form['scenario'])

    def _card_save(self):
        from windows_ui.character_cards import profile_from_fields
        from windows_ui import core_characters as cards
        try:
            profile=profile_from_fields(self.play_character_name.text(),
                 self.play_character_description.toPlainText(),
                 self.play_appearance.toPlainText(),self.play_speech.toPlainText(),
                 self.play_scenario.toPlainText())
        except ValueError:
            self.play_state.setText(self.t('Имя и суммарное описание персонажа (до 4000 символов) обязательны.','The name and the combined description of the character (up to 4000 characters) are required.')); return
        name=self.play_character_name.text().strip()
        selected=self._card_records.get(self.play_card_selector.currentData())
        # Editing the loaded card keeps its id (and the client's other fields).
        record_id=selected['id'] if selected and selected['data'].get('name')==name else None
        def save(progress, cancel):
            return {'status':'PASS','id':cards.save_card(self.root,name,profile,record_id)}
        def done(result):
            if result.get('status')!='PASS':
                self.play_state.setText(self.t('Карточка не сохранена: EGO Core недоступен.','Card not saved: EGO Core is unavailable.')); return
            self._card_refresh(result['id'])
            self.play_state.setText(self.t('Карточка сохранена в EGO Core (зашифрованная база).','Card saved to EGO Core (encrypted database).'))
        self._submit(save, done)

    def _card_delete(self):
        from windows_ui import core_characters as cards
        record=self._card_records.get(self.play_card_selector.currentData())
        if not record: return
        name=record['data'].get('name',record['id'])
        if QMessageBox.question(self,self.t('Удалить карточку','Remove card'),self.t('Удалить карточку «{name}»? Персонаж исчезнет и из EGO Client.','Remove the card "{name}"? The character will disappear from EGO Client too.').replace('{name}',name)) != QMessageBox.StandardButton.Yes: return
        def delete(progress, cancel):
            cards.delete_card(self.root,record['id']); return {'status':'PASS'}
        def done(result):
            if result.get('status')!='PASS':
                self.play_state.setText(self.t('Карточка не удалена: EGO Core недоступен.','Card not deleted: EGO Core is unavailable.')); return
            self._card_refresh()
            self.play_state.setText(self.t('Карточка удалена.','Card removed.'))
        self._submit(delete, done)

    def _play_refresh(self):
        if self._play_task is not None: return
        self._card_refresh(self.play_card_selector.currentData())
        from windows_ui.playground import list_chats
        def done(result):
            if result.get('status')=='FAIL':
                self.play_state.setText(result['code']); return
            chats=result['chats']
            selected=self._play_chat_id
            self.play_chats.blockSignals(True); self.play_chats.clear()
            self.play_chats.addItem(self.t('Новый диалог (создаётся при отправке)','New chat (created on send)'),None)
            for item in chats:
                self.play_chats.addItem(item.get('title','Chat')+' · '+item['id'][:8],item['id'])
            index=self.play_chats.findData(selected) if selected else 0
            self.play_chats.setCurrentIndex(max(0,index)); self.play_chats.blockSignals(False)
            self._play_select()
        def operation(progress,cancel):
            try: return {'status':'PASS','chats':list_chats(self.root)}
            except Exception as exc: return {'status':'FAIL','code':type(exc).__name__}
        self._submit(operation,done)

    def _play_select(self,*_):
        if self._play_task is not None: return
        self._play_chat_id=self.play_chats.currentData()
        self.play_history.clear(); self.play_answer.clear(); self._play_messages=[]
        if not self._play_chat_id: return
        chat_id=self._play_chat_id
        from windows_ui.playground import history
        def operation(progress,cancel):
            try: return {'status':'PASS','messages':history(self.root,chat_id)}
            except Exception as exc: return {'status':'FAIL','code':type(exc).__name__}
        def done(result):
            if self._play_chat_id!=chat_id: return
            if result['status']!='PASS': self.play_state.setText(result['code']); return
            from windows_ui.chat_format import render_conversation
            self._play_messages=list(result['messages'])
            self.play_history.setHtml(render_conversation(self._play_messages))
            cursor=self.play_history.textCursor(); cursor.movePosition(cursor.MoveOperation.End)
            self.play_history.setTextCursor(cursor)
        self._submit(operation,done)

    def _play_new_chat(self):
        if self._play_task is not None: return
        self._play_chat_id=None
        self.play_chats.setCurrentIndex(0)
        self.play_history.clear(); self.play_answer.clear(); self.play_prompt.clear(); self._play_messages=[]
        self.play_state.setText(self.t('Новый диалог будет создан при отправке.','New chat will be created on send.'))

    def _play_prompt_preview(self):
        name=self.play_character_name.text().strip()
        from windows_ui.character_cards import profile_from_fields
        try:
            profile=profile_from_fields(name,self.play_character_description.toPlainText(),
                self.play_appearance.toPlainText(),self.play_speech.toPlainText(),
                self.play_scenario.toPlainText())
            QMessageBox.information(self,self.t('Инструкции персонажа','Character instructions'),
                self.t('Персонаж: ','Character: ')+name+'\n\n'+profile['description']+'\n\n'
                +self.t('Размер описания: {n}/4000 символов','Description size: {n}/4000 characters').replace('{n}',str(len(profile['description']))))
        except ValueError:
            self.play_state.setText(self.t('Проверь имя, описание и общий размер профиля (до 4000 символов).','Check the name, the description and the total profile size (up to 4000 characters).'))

    def _play_regenerate(self):
        if self._play_task is not None or not self._play_chat_id: return
        if not self._play_messages or self._play_messages[-1].get('role')!='assistant':
            self.play_state.setText(self.t('Нет последнего ответа AI для регенерации.','No last AI reply to regenerate.')); return
        if QMessageBox.question(self,'EGO',self.t('Перегенерировать последний ответ? Старый будет заменён только после успешной генерации.','Regenerate the last reply? The old one is replaced only after a successful generation.')) != QMessageBox.StandardButton.Yes:
            return
        self._play_start(regenerate=True)

    def _play_continue(self):
        if self._play_task is not None or not self._play_chat_id: return
        if not self._play_messages or self._play_messages[-1].get('role')!='assistant':
            self.play_state.setText(self.t('Нет последнего ответа AI для продолжения.','No last AI reply to continue.')); return
        self._play_start(continuing=True)

    def _play_start(self, regenerate=False, continuing=False):
        if self._play_task is not None: return
        prompt='' if (regenerate or continuing) else self.play_prompt.toPlainText().strip()
        if not prompt and not regenerate and not continuing: self.play_state.setText(self.t('Введите сообщение.','Enter a message.')); return
        if not self._online: self.play_state.setText(self.t('Сначала запустите Core.','Start Core first.')); return
        from windows_ui.playground import generate
        chat_id=self._play_chat_id
        char_name=self.play_character_name.text().strip() or None
        char_description=self.play_character_description.toPlainText().strip() or None
        if char_name and char_description:
            from windows_ui.character_cards import profile_from_fields
            try:
                char_description=profile_from_fields(char_name,char_description,
                    self.play_appearance.toPlainText(),self.play_speech.toPlainText(),
                    self.play_scenario.toPlainText())['description']
            except ValueError:
                self.play_state.setText(self.t('Общий объём профиля превышает 4000 символов.','The total profile size exceeds 4000 characters.')); return
        if char_name is not None or char_description is not None:
            if not char_name or not char_description or len(char_description)>4000:
                self.play_state.setText(self.t('Укажите имя и описание персонажа (макс. 4000 символов).','Enter the character name and description (max. 4000 characters).')); return
        self.play_answer.clear(); self._play_pending_prompt=prompt or None
        self._play_regenerating=regenerate
        self._play_continuing=continuing
        self.play_send.setEnabled(False); self.play_regen.setEnabled(False); self.play_continue.setEnabled(False); self.play_stop.setEnabled(True)
        self.play_chats.setEnabled(False)
        self._play_started=time.monotonic()
        self.play_state.setText(self.t('Генерация...','Generating...'))
        self._play_task=self._submit(lambda progress,cancel:generate(self.root,prompt,progress,cancel,chat_id=chat_id,character_name=char_name,character_description=char_description,regenerate=regenerate,continuing=continuing),
                                     self._play_finished,self._play_progress)

    def _play_progress(self,event):
        self.play_answer.setPlainText(event.get('text',''))
        from windows_ui.chat_format import render_conversation
        pending=list(self._play_messages[:-1] if self._play_regenerating else self._play_messages)
        if self._play_pending_prompt: pending.append({'role':'user','content':self._play_pending_prompt})
        if event.get('text'):
            if self._play_continuing and pending and pending[-1].get('role')=='assistant':
                pending[-1]=dict(pending[-1],content=pending[-1]['content']+event['text'])
            else: pending.append({'role':'assistant','content':event['text']})
        self.play_history.setHtml(render_conversation(pending))
        bar=self.play_history.verticalScrollBar(); bar.setValue(bar.maximum())
        self.play_state.setText(str(event.get('state','generating'))+' · '+f'{time.monotonic()-self._play_started:.1f} s'+' · '+str(len(event.get('text','')))+' '+self.t('симв.','chars'))

    def _play_stop(self):
        if self._play_task:
            self._play_task.cancel.set()
            self.play_stop.setEnabled(False)
            self.play_state.setText(self.t('Отправляется запрос остановки...','Requesting stop...'))

    def _play_finished(self,result):
        self._play_task=None; self._play_pending_prompt=None; self.play_send.setEnabled(True); self.play_regen.setEnabled(True); self.play_continue.setEnabled(True); self.play_stop.setEnabled(False)
        self.play_chats.setEnabled(True)
        meta=result.get('metrics') or {}
        bits=[str(result.get('status','FAIL')),str(result.get('code','UNKNOWN')),
              str(result.get('seconds',round(time.monotonic()-self._play_started,1)))+' s']
        if result.get('finish_reason'):
            reason=str(result['finish_reason'])
            bits.append(self.t('Лимит ответа достигнут — можно продолжить','Reply limit reached — you can continue') if reason=='length' else 'finish: '+reason)
        if type(meta.get('completion_tokens')) is int: bits.append('tokens: '+str(meta['completion_tokens']))
        if type(meta.get('prompt_tokens')) is int: bits.append('prompt: '+str(meta['prompt_tokens']))
        if isinstance(meta.get('tokens_per_second'),(int,float)): bits.append('t/s: '+str(meta['tokens_per_second']))
        self.play_state.setText(' · '.join(bits))
        if result.get('chat_id'):
            self._play_chat_id=result['chat_id']
            self.play_prompt.clear()
            self._play_refresh()

    def _settings(self):
        page=self.page('Настройки','Settings'); form=QFormLayout()
        self.language_combo=QComboBox(); self.language_combo.addItems([n for _,n in LANGUAGES]); form.addRow(self.label('Язык','Language'),self.language_combo)
        self.theme_combo=QComboBox(); self.theme_combo.addItems(['Dark','Light']); form.addRow(self.label('Тема','Theme'),self.theme_combo)
        self.reduced_effects=QCheckBox(); form.addRow(self.label('Уменьшить эффекты','Reduce effects'),self.reduced_effects)
        self.close_combo=QComboBox(); form.addRow(self.label('При закрытии','On close'),self.close_combo)
        for ru,en,text in [('Данные','Data',str(self.root/'data')),('Логи','Logs',str(self.root/'logs')),('Профили','Profiles',str(self.root/'settings'))]:
            field=QLineEdit(text); field.setReadOnly(True); form.addRow(self.label(ru,en),field)
        page.addLayout(form)
        page.addWidget(self.label('Порты и параметры видеокарты задаются в профиле модели (Запуск → Дополнительно).','Ports and GPU options are set in the model profile (Launch → More).',muted=True))
        page.addWidget(self.button('Сохранить настройки','Save settings',self.save_preferences,primary=True))
        page.addStretch()
        gui=self.snapshot['gui']; self.language_combo.setCurrentIndex(max(0,[k for k,_ in LANGUAGES].index(gui['language']) if gui['language'] in [k for k,_ in LANGUAGES] else 0)); self.theme_combo.setCurrentIndex(0 if gui['theme']=='dark' else 1); self.reduced_effects.setChecked(gui['reduced_effects'])
        self._close_preference=gui['close_behavior']

    def _access_network(self):
        page=self.page('Домашняя сеть','Home network')
        # Stage 4i: home-network access for a laptop (HTTPS + EGO account sign-in).
        lan_box=self.group('Доступ с ноутбука в домашней сети','Laptop access on the home network'); lan_form=QFormLayout(lan_box)
        self.lan_enabled=QCheckBox(); self.lan_enabled.setObjectName('lan_enabled')
        lan_form.addRow(self.label('Разрешить вход по HTTPS','Allow HTTPS sign-in'),self.lan_enabled)
        self.lan_port=QSpinBox(); self.lan_port.setRange(1024,65535); self.lan_port.setObjectName('lan_port')
        lan_form.addRow(self.label('Порт','Port'),self.lan_port)
        self.lan_info=QLabel(''); self.lan_info.setWordWrap(True); self.lan_info.setObjectName('lanInfo')
        self.lan_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lan_form.addRow(self.lan_info)
        lan_form.addRow(self.label('На ноутбуке войдите аккаунтом из вкладки «Доступ → Аккаунты». Браузер один раз предупредит о сертификате: сверьте отпечаток и продолжите. При первом включении Windows может спросить разрешение для сети — разрешите только для частной сети.',
                                   'On the laptop, sign in with an account from Access → Accounts. The browser warns about the certificate once: compare the fingerprint and continue. Windows may ask for network permission: allow private networks only.',muted=True))
        lan_form.addRow(self.button('Сохранить доступ из сети','Save network access',self.save_network,action=True))
        page.addWidget(lan_box)
        self._load_network()
        page.addStretch()

    def _access_tunnel(self):
        page=self.page('Туннель','Tunnel')
        # 2026-11-06: access from outside the home network — Cloudflare quick tunnel + secret words.
        tun_box=self.group('Доступ вне дома (туннель)','Access from outside home (tunnel)'); tun_layout=QVBoxLayout(tun_box)
        self.tunnel_info=QLabel(''); self.tunnel_info.setWordWrap(True); self.tunnel_info.setObjectName('tunnelInfo')
        self.tunnel_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tun_layout.addWidget(self.tunnel_info)
        self.tunnel_link=QLineEdit(); self.tunnel_link.setReadOnly(True); self.tunnel_link.setObjectName('tunnelLink'); self.tunnel_link.hide()
        tun_layout.addWidget(self.tunnel_link)
        tun_row=QHBoxLayout()
        self.tunnel_toggle=QPushButton(self.t('Включить туннель','Turn the tunnel on')); self.tunnel_toggle.setObjectName('primary'); self.tunnel_toggle.clicked.connect(self.toggle_tunnel)
        self.tunnel_copy=QPushButton(self.t('Скопировать ссылку','Copy link')); self.tunnel_copy.clicked.connect(lambda:QApplication.clipboard().setText(self.tunnel_link.text())); self.tunnel_copy.hide()
        tun_row.addWidget(self.tunnel_toggle); tun_row.addWidget(self.tunnel_copy); tun_row.addStretch(); tun_layout.addLayout(tun_row)
        tun_layout.addWidget(self.label('Ссылка открывает EGO из любого места, например с телефона на улице. В ней адрес Cloudflare и секрет из случайных слов через точку: без слов страница не откроется. Дальше — вход аккаунтом EGO (почта и пароль); управление сервером доступно только на этом ПК. При каждом включении адрес и слова новые. Не пересылайте ссылку другим и выключайте туннель, когда он не нужен.',
                                        'The link opens EGO from anywhere, e.g. a phone outside. It holds a Cloudflare address and a secret of random words joined by dots: without the words nothing opens. Then sign in with an EGO account (email and password); server controls stay on this PC. Every start gives a new address and words. Do not share the link; turn the tunnel off when not needed.',muted=True))
        page.addWidget(tun_box)
        self._tunnel_state='off'
        self.tunnel_timer=QTimer(self); self.tunnel_timer.setInterval(1500); self.tunnel_timer.timeout.connect(self.refresh_tunnel)
        QTimer.singleShot(800,self.refresh_tunnel)
        page.addStretch()

    # ---- accounts (2026-11-18): who may sign in from other devices ------------------------------------
    def _accounts(self):
        page=self.page('Аккаунты','Accounts')
        page.addWidget(self.label('Кто может входить в EGO с других устройств: ноутбук, телефон. Укажите почту и пароль и нажмите «Добавить»; можно добавить друзей. Почта служит только логином, письма не отправляются. Все аккаунты видят одни и те же истории и персонажей этой установки.',
                                  'Who can sign in to EGO from other devices: laptop, phone. Enter an email and a password and press Add; you can add friends. The email is only a login, nothing is sent. All accounts see the same stories and characters of this installation.',muted=True))
        self.users_table=QTableWidget(0,4)
        self.users_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.users_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.users_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.users_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.users_table.setMinimumHeight(170); self.users_table.verticalHeader().hide()
        self.users_table.setHorizontalHeaderLabels([self.t('Почта','Email'),self.t('Создан','Created'),self.t('Устройств','Devices'),self.t('Статус','Status')])
        self.users_table.itemSelectionChanged.connect(self._user_selected)
        page.addWidget(self.users_table)
        form=QFormLayout()
        self.user_email=QLineEdit(); self.user_email.setMaxLength(254)
        self.user_password=QLineEdit(); self.user_password.setMaxLength(256); self.user_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(self.label('Почта','Email'),self.user_email)
        form.addRow(self.label('Пароль (от 10 символов)','Password (10+ characters)'),self.user_password)
        page.addLayout(form)
        row=QHBoxLayout()
        row.addWidget(self.button('Добавить','Add',self.add_user,primary=True))
        row.addWidget(self.button('Сменить пароль','Change password',self.change_user_password))
        row.addWidget(self.button('Удалить','Remove',self.remove_user))
        row.addWidget(self.button('Обновить','Refresh',self.refresh_users)); row.addStretch(); page.addLayout(row)
        self.users_notice=QLabel(''); self.users_notice.setWordWrap(True); page.addWidget(self.users_notice)
        page.addStretch()
        self._users_loaded=False
        QTimer.singleShot(1200,self.refresh_users)

    def _users_call(self,method,path,payload=None,message=None):
        from server.ego_core import control
        def operation(progress,cancel):
            try: return {'status':'PASS','data':control.request(self.root,method,path,payload=payload,timeout=20)}
            except Exception as exc: return {'status':'FAIL','code':getattr(exc,'code','CORE_REQUEST_FAILED')}
        self._submit(operation,lambda result:self._users_done(result,message))

    def _users_done(self,result,message):
        from server.ego_core.errors import describe
        if result.get('status')!='PASS' or not isinstance(result.get('data'),dict):
            code=result.get('code') or 'CORE_REQUEST_FAILED'
            if str(code).startswith('CORE_'):
                self.users_notice.setText(self.t('Запустите сервер (вкладка «Общее»): аккаунты хранятся в базе Core.','Start the server (General tab): accounts are stored in the Core database.'))
            else:
                text,hint=describe(code,self.lang); self.users_notice.setText(text+' '+hint)
            return
        users=result['data'].get('users',[])
        self.users_table.setHorizontalHeaderLabels([self.t('Почта','Email'),self.t('Создан','Created'),self.t('Устройств','Devices'),self.t('Статус','Status')])
        self.users_table.setRowCount(len(users))
        for i,user in enumerate(users):
            created=time.strftime('%d.%m.%Y',time.localtime(user.get('created_at') or 0))
            status=self.t('Заблокирован','Locked') if user.get('locked') else self.t('Активен','Active')
            for j,value in enumerate([user.get('email',''),created,str(user.get('sessions',0)),status]):
                self.users_table.setItem(i,j,QTableWidgetItem(value))
        self._users_loaded=True
        self.users_notice.setText(self.t(message[0],message[1]) if message else '')

    def refresh_users(self):
        self._users_call('GET','/v1/accounts/users')

    def _user_selected(self):
        rows=self.users_table.selectionModel().selectedRows()
        if rows: self.user_email.setText(self.users_table.item(rows[0].row(),0).text())

    def add_user(self):
        email,password=self.user_email.text().strip(),self.user_password.text()
        if not email or not password:
            self.users_notice.setText(self.t('Введите почту и пароль.','Enter an email and a password.')); return
        self.user_password.clear()
        self._users_call('POST','/v1/accounts/users',payload={'email':email,'password':password},message=('Аккаунт добавлен.','Account added.'))

    def change_user_password(self):
        email,password=self.user_email.text().strip(),self.user_password.text()
        if not email or not password:
            self.users_notice.setText(self.t('Выберите аккаунт в списке и введите новый пароль.','Select an account in the list and enter a new password.')); return
        self.user_password.clear()
        self._users_call('POST','/v1/accounts/users/password',payload={'email':email,'password':password},
                         message=('Пароль изменён, этот аккаунт вышел на всех устройствах.','Password changed; this account was signed out everywhere.'))

    def remove_user(self):
        email=self.user_email.text().strip()
        if not email:
            self.users_notice.setText(self.t('Выберите аккаунт в списке.','Select an account in the list.')); return
        answer=QMessageBox.question(self,self.t('Удалить аккаунт?','Remove the account?'),email)
        if answer!=QMessageBox.StandardButton.Yes: return
        self.user_email.clear()
        self._users_call('POST','/v1/accounts/users/remove',payload={'email':email},message=('Аккаунт удалён.','Account removed.'))

    # ---- dependencies (2026-11-18) ----------------------------------------------------------------------
    def _dependencies(self):
        page=self.page('Зависимости','Dependencies')
        page.addWidget(self.label('Что нужно EGO для работы. «Нужно» — без этого сервер не запустится, «По желанию» — для дополнительных функций.',
                                  'What EGO needs to run. Required: the server will not start without it. Optional: for extra features.',muted=True))
        self.dependency_grid=QGridLayout(); self.dependency_grid.setColumnStretch(3,1); page.addLayout(self.dependency_grid)
        row=QHBoxLayout()
        row.addWidget(self.button('Проверить заново','Check again',self.refresh_dependencies,primary=True))
        row.addWidget(self.button('Мастер первоначальной настройки','Setup wizard',self.show_setup)); row.addStretch(); page.addLayout(row)
        row=QHBoxLayout()
        row.addWidget(self.button('Проверка компьютера (Python, Windows, GPU)','Computer check (Python, Windows, GPU)',lambda:self.run_action('environment'),action=True)); row.addStretch(); page.addLayout(row)
        page.addWidget(self.label('Озвучка ставится командой scripts\\install_tts.py, туннель использует cloudflared.exe в runtime\\tunnel, картинки — отдельный ComfyUI.',
                                  'Voice output is installed with scripts\\install_tts.py, the tunnel uses cloudflared.exe in runtime\\tunnel, pictures need a separate ComfyUI.',muted=True))
        page.addStretch()
        self.refresh_dependencies()

    def _dependency_items(self):
        import importlib.util, shutil, socket
        ok_python=getattr(sys,'frozen',False) or (sys.version_info[:2]==(3,12) and sys.maxsize>2**32)
        binary=self.model_fields['binary'].text().strip() if hasattr(self,'model_fields') else ''
        model=self.model_fields['model'].text().strip() if hasattr(self,'model_fields') else ''
        gpu=bool(shutil.which('nvidia-smi')) or Path(r'C:\Windows\System32\nvidia-smi.exe').exists()
        cloudflared=(self.root/'runtime'/'tunnel'/'cloudflared.exe').is_file()
        try:
            from server.ego_core import tts
            voice=bool(tts.installed())
        except Exception: voice=False
        comfy=False
        try:
            with socket.create_connection(('127.0.0.1',8188),timeout=0.25): comfy=True
        except OSError: pass
        return [
            ('Python 3.12 (64 бит)','Python 3.12 (64-bit)',True,ok_python,sys.version.split()[0],'Установите Python 3.12 x64.','Install Python 3.12 x64.'),
            ('llama-server.exe','llama-server.exe',True,bool(binary) and Path(binary).is_file(),binary,'Укажите файл во вкладке «Запуск».','Choose the file in the Launch tab.'),
            ('Модель (GGUF)','Model (GGUF)',True,bool(model) and Path(model).is_file(),model,'Выберите модель во вкладке «Запуск».','Choose a model in the Launch tab.'),
            ('Видеокарта NVIDIA (nvidia-smi)','NVIDIA graphics card (nvidia-smi)',True,gpu,'','Установите драйвер NVIDIA.','Install the NVIDIA driver.'),
            ('SQLCipher (шифрование базы)','SQLCipher (database encryption)',True,importlib.util.find_spec('sqlcipher3') is not None,'','Запустите tools\\windows\\01_SETUP.bat.','Run tools\\windows\\01_SETUP.bat.'),
            ('Озвучка (XTTS)','Voice output (XTTS)',False,voice,'','Установка: py -3.12 scripts\\install_tts.py','Install: py -3.12 scripts\\install_tts.py'),
            ('Туннель (cloudflared.exe)','Tunnel (cloudflared.exe)',False,cloudflared,'','Положите cloudflared.exe в runtime\\tunnel.','Put cloudflared.exe into runtime\\tunnel.'),
            ('Картинки (ComfyUI, порт 8188)','Pictures (ComfyUI, port 8188)',False,comfy,'','Запустите ComfyUI, если нужны картинки.','Start ComfyUI if you need pictures.'),
        ]

    def refresh_dependencies(self):
        grid=self.dependency_grid
        while grid.count():
            item=grid.takeAt(0); widget=item.widget()
            if widget is not None: widget.deleteLater()
        for r,(ru,en,required,ok,detail,hint_ru,hint_en) in enumerate(self._dependency_items()):
            grid.addWidget(QLabel(self.t(ru,en)),r,0)
            grid.addWidget(QLabel(self.t('Нужно','Required') if required else self.t('По желанию','Optional')),r,1)
            status=QLabel(('✔ '+self.t('Найдено','Found')) if ok else ('✖ '+self.t('Нет','Missing')))
            status.setStyleSheet('color:#6fcf97;font-weight:600' if ok else ('color:#eb5757;font-weight:600' if required else 'color:#e2b45a;font-weight:600'))
            grid.addWidget(status,r,2)
            note=QLabel(detail if ok and detail else ('' if ok else self.t(hint_ru,hint_en))); note.setWordWrap(True); note.setObjectName('muted')
            note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            note.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred); note.setMinimumWidth(120)
            grid.addWidget(note,r,3)

    def _load_network(self):
        from server.ego_core import lan
        settings=lan.load_settings(self.root)
        self.lan_enabled.setChecked(settings['lan_access']); self.lan_port.setValue(settings['lan_port'])
        self._show_network(settings)

    def _show_network(self,settings):
        from server.ego_core import lan
        from server.ego_core.errors import describe
        status=lan.read_status(self.root) or {}
        lines=[]
        if not settings['lan_access']:
            lines.append(self.t('Выключено: EGO доступен только на этом ПК.','Off: EGO is reachable from this PC only.'))
        elif status.get('enabled') and status.get('port')==settings['lan_port'] and (status.get('urls') or status.get('error')):
            if status.get('urls'):
                lines.append(self.t('Адрес для ноутбука: ','Laptop address: ')+'  '.join(status['urls']))
                lines.append(self.t('Отпечаток сертификата (SHA-256): ','Certificate fingerprint (SHA-256): ')+str(status.get('fingerprint') or '—'))
            if status.get('error'):
                message,hint=describe(status['error'],self.lang)
                lines.append(message+' '+hint)
        else:
            lines.append(self.t('Включится, когда Core будет запущен или перезапущен.','Takes effect when Core starts or restarts.'))
        self.lan_info.setText('\n'.join(lines))

    def save_network(self):
        from server.ego_core import lan
        try:
            settings=lan.save_settings(self.root,lan_access=self.lan_enabled.isChecked(),lan_port=self.lan_port.value())
        except (OSError,ValueError):
            self.lan_info.setText(self.t('Не удалось сохранить settings\\network.json.','Could not save settings\\network.json.')); return
        self._show_network(settings)
        running=lan.read_status(self.root) or {}
        if bool(running.get('enabled'))!=settings['lan_access'] or (settings['lan_access'] and running.get('port')!=settings['lan_port']):
            self.lan_info.setText(self.lan_info.text()+'\n'+self.t('Сохранено. Запустите или перезапустите Core, чтобы применить.','Saved. Start or restart Core to apply.'))

    # ---- tunnel (2026-11-06) --------------------------------------------------------------------
    def refresh_tunnel(self):
        from server.ego_core import control
        if getattr(self,'_tunnel_busy',False): return
        self._tunnel_busy=True
        def operation(progress,cancel):
            try: return {'status':'PASS','tunnel':control.request(self.root,'GET','/v1/tunnel',timeout=2)}
            except Exception as exc: return {'status':'FAIL','code':getattr(exc,'code','CORE_REQUEST_FAILED')}
        self._submit(operation,self._show_tunnel)

    def toggle_tunnel(self):
        from server.ego_core import control
        turning_on=self._tunnel_state not in ('on','starting')
        if turning_on:
            answer=QMessageBox.question(self,self.t('Включить туннель?','Turn the tunnel on?'),
                self.t('EGO станет доступен из интернета по секретной ссылке. Войти можно только аккаунтом EGO (почта и пароль). Трафик идёт через Cloudflare. Включить?',
                       'EGO becomes reachable from the internet through a secret link. Only an EGO account (email and password) can sign in. Traffic goes through Cloudflare. Turn it on?'))
            if answer!=QMessageBox.StandardButton.Yes: return
        self.tunnel_toggle.setEnabled(False)
        path='/v1/tunnel/start' if turning_on else '/v1/tunnel/stop'
        def operation(progress,cancel):
            try: return {'status':'PASS','tunnel':control.request(self.root,'POST',path,payload={},timeout=15)}
            except Exception as exc: return {'status':'FAIL','code':getattr(exc,'code','CORE_REQUEST_FAILED')}
        self._submit(operation,self._show_tunnel)

    def _show_tunnel(self,result):
        from server.ego_core.errors import describe
        self._tunnel_busy=False
        self.tunnel_toggle.setEnabled(True)
        if result.get('status')!='PASS' or not isinstance(result.get('tunnel'),dict):
            self._tunnel_state='off'; self.tunnel_timer.stop()
            self.tunnel_info.setText(self.t('Core не запущен. Туннель включается, когда Core работает.','Core is not running. The tunnel can be turned on while Core runs.'))
            self.tunnel_toggle.setText(self.t('Включить туннель','Turn the tunnel on')); self.tunnel_toggle.setEnabled(False)
            self.tunnel_link.hide(); self.tunnel_copy.hide(); return
        data=result['tunnel']; state=data.get('state') or 'off'; self._tunnel_state=state
        on=state in ('on','starting')
        self.tunnel_toggle.setText(self.t('Выключить туннель','Turn the tunnel off') if on else self.t('Включить туннель','Turn the tunnel on'))
        if state=='on' and data.get('url'):
            self.tunnel_info.setText(self.t('Туннель открыт. Ссылка для телефона или ноутбука вне дома:','The tunnel is open. Link for a phone or laptop outside:'))
            if self.tunnel_link.text()!=data['url']: self.tunnel_link.setText(data['url'])
            self.tunnel_link.show(); self.tunnel_copy.show()
            self.tunnel_timer.setInterval(10000); self.tunnel_timer.start()          # notice if Cloudflare drops it
        elif state=='starting':
            self.tunnel_info.setText(self.t('Открываю туннель через Cloudflare… обычно 5–15 секунд.','Opening the tunnel through Cloudflare… usually 5–15 seconds.'))
            self.tunnel_link.hide(); self.tunnel_copy.hide()
            self.tunnel_timer.setInterval(1500); self.tunnel_timer.start()
        else:
            self.tunnel_timer.stop(); self.tunnel_link.hide(); self.tunnel_copy.hide(); self.tunnel_link.clear()
            if state=='error' and data.get('error'):
                message,hint=describe(data['error'],self.lang); self.tunnel_info.setText(message+' '+hint)
            elif not data.get('available',True):
                message,hint=describe('TUNNEL_BINARY_MISSING',self.lang); self.tunnel_info.setText(message+' '+hint)
            else:
                self.tunnel_info.setText(self.t('Выключен: снаружи EGO недоступен.','Off: EGO is not reachable from outside.'))

    def _retranslate_dynamic(self):
        """Texts that were set once with self.t(...) when the window was built, or by a refresh."""
        for widget,method,ru,en in [
                (getattr(self,'play_character_toggle',None),'setText','Персонаж','Character'),
                (getattr(self,'play_answer_toggle',None),'setText','Текст ответа без оформления','Raw reply text'),
                (getattr(self,'models_advanced_toggle',None),'setText','Дополнительно: порты, процессор, проверка, импорт','More: ports, CPU, checks, import'),
                (getattr(self,'tunnel_copy',None),'setText','Скопировать ссылку','Copy link'),
                (getattr(self,'play_history',None),'setPlaceholderText','История сообщений появится здесь.','Message history appears here.'),
                (getattr(self,'play_prompt',None),'setPlaceholderText','Напишите следующее сообщение...','Write the next message...'),
                (getattr(self,'play_answer',None),'setPlaceholderText','Текущий ответ модели...','Current model response...'),
                (getattr(self,'play_character_name',None),'setPlaceholderText','Имя персонажа (необязательно)','Character name (optional)'),
                (getattr(self,'play_character_description',None),'setPlaceholderText','Характер, стиль речи, правила управления ролью (до 4000 символов).','Personality, speech style, role rules (up to 4000 characters).'),
                (getattr(self,'play_appearance',None),'setPlaceholderText','Внешность персонажа (необязательно)','Character appearance (optional)'),
                (getattr(self,'play_speech',None),'setPlaceholderText','Манера речи (необязательно)','Manner of speech (optional)'),
                (getattr(self,'play_scenario',None),'setPlaceholderText','Стартовая сцена (необязательно)','Opening scene (optional)')]:
            if widget is not None: getattr(widget,method)(self.t(ru,en))
        for combo,ru,en in [(getattr(self,'play_chats',None),'Новый диалог (создаётся при отправке)','New chat (created on send)'),
                            (getattr(self,'model_choice',None),'— выберите модель —','— choose a model —'),
                            (getattr(self,'play_card_selector',None),'Карточки персонажей (выбрать)','Character cards (choose)')]:
            if combo is not None and combo.count() and combo.itemData(0) is None: combo.setItemText(0,self.t(ru,en))
        combo=getattr(self,'profile_combo',None)
        if combo is not None:
            from server.ego_core.presets import MODEL_PRESETS
            for i in range(combo.count()):
                data=combo.itemData(i)
                if isinstance(data,str) and data.startswith('preset:'):
                    item=next((p for p in MODEL_PRESETS if p['id']==data.split(':',1)[1]),None)
                    if item: combo.setItemText(i,self.t('Готовая: ','Ready: ')+self.t(*item['name']))
                else:
                    combo.setItemText(i,self._display_name(combo.itemText(i)))
            if hasattr(self,'profile_name'): self.profile_name.setText(self._display_name(self.profile_name.text()))
        table=getattr(self,'users_table',None)
        if table is not None:
            table.setHorizontalHeaderLabels([self.t('Почта','Email'),self.t('Создан','Created'),self.t('Устройств','Devices'),self.t('Статус','Status')])
            if getattr(self,'_users_loaded',False): self.refresh_users()
        if hasattr(self,'dependency_grid'): self.refresh_dependencies()
        if hasattr(self,'lan_info'):
            try:
                from server.ego_core import lan
                self._show_network(lan.load_settings(self.root))
            except Exception: pass
        if hasattr(self,'tunnel_info'): self.refresh_tunnel()
        if hasattr(self,'profile_info') and hasattr(self,'model_fields'):
            from server.ego_core.presets import model_preset
            data=self.profile_combo.currentData()
            try:
                if isinstance(data,str) and data.startswith('preset:'):
                    self.profile_info.setText(self._ready_info(model_preset(data.split(':',1)[1]),self.model_fields['model'].text().strip()))
                else: self._describe_profile(self.profile_name.text())
                self._show_model_size(self.model_fields['model'].text().strip())
            except Exception: pass
        if hasattr(self,'play_card_selector'):
            try: self._card_refresh(self.play_card_selector.currentData())
            except Exception: pass
        if hasattr(self,'preset_combo'): self._preset_changed()

    def _retranslate(self):
        for widget,method,ru,en in self._translations: getattr(widget,method)(self.t(ru,en))
        self._retranslate_dynamic()
        row=max(0,self.navigation.currentRow()); self.navigation.clear()
        for ru,en in NAV: self.navigation.addItem(self.t(ru,en))
        self.navigation.setCurrentRow(row)
        action=self.test_combo.currentData(); self.test_combo.clear()
        for key,ru,en in ACTIONS: self.test_combo.addItem(self.t(ru,en),key)
        index=self.test_combo.findData(action); self.test_combo.setCurrentIndex(max(0,index))
        behavior=self.close_combo.currentData() or getattr(self,'_close_preference','ask'); self.close_combo.clear()
        for key,ru,en in [('ask','Спрашивать','Ask'),('hide','Скрыть, Core продолжает работу','Hide, leave Core running'),('stop','Остановить Core и закрыть','Stop Core and close')]: self.close_combo.addItem(self.t(ru,en),key)
        self.close_combo.setCurrentIndex(max(0,self.close_combo.findData(behavior)))
        self.help_text.setText('\n\n'.join(code+' — '+self.t(*ERROR_HELP[code]) for code in ['PORT_IN_USE','MODEL_NOT_PROVIDED','RUNTIME_NOT_PROVIDED','GPU_OFFLOAD_NOT_VERIFIED','CORE_LOCKED']))
        if hasattr(self,'preset_combo'):
            from server.ego_core.presets import preset
            for i in range(self.preset_combo.count()):
                self.preset_combo.setItemText(i,self.t(*preset(self.preset_combo.itemData(i))['name']))
            self._preset_changed()
        if getattr(self,'_profiles_loaded',False) and not self._dirty: self.reload_profiles()

    def _changed(self,*args):
        if not self._loading:
            self._dirty=True; self.model_notice.setText(self.t('Есть несохранённые изменения.','Unsaved changes.'))

    def _values(self):
        values={**getattr(self,'_form_base',{}),'host':'127.0.0.1'}
        for key,field in self.model_fields.items():
            if isinstance(field,QLineEdit): values[key]=field.text().strip() or None
            elif isinstance(field,QComboBox): values[key]={0:None,1:True,2:False}[field.currentIndex()]
            else: values[key]=field.value()
        for key in ('threads','batch_size'):
            if values[key]==0: values[key]=None
        return values

    def _display_name(self,name):
        """A stored profile that carries a ready-made preset's name is shown in the window language."""
        from server.ego_core.presets import MODEL_PRESETS
        key=(name or '').casefold()
        for item in MODEL_PRESETS:
            if key in self._preset_names(item): return self.t(*item['name'])
        return name

    def reload_profiles(self):
        from server.ego_core.presets import MODEL_PRESETS
        self.snapshot=self.configuration.snapshot(); selected=self._active_profile or self.snapshot['selected_id']
        self._loading=True; self.profile_combo.blockSignals(True); self.profile_combo.clear()
        names=set()
        for profile in self.snapshot['profiles']:
            self.profile_combo.addItem(self._display_name(profile['name']),profile['id']); names.add(profile['name'].casefold())
        ready=[item for item in MODEL_PRESETS if not (self._preset_names(item) & names)]
        if ready:
            if self.profile_combo.count(): self.profile_combo.insertSeparator(self.profile_combo.count())
            for item in ready: self.profile_combo.addItem(self.t('Готовая: ','Ready: ')+self.t(*item['name']),'preset:'+item['id'])
        self.profile_combo.setCurrentIndex(max(0,self.profile_combo.findData(selected))); self.profile_combo.blockSignals(False); self._loading=False
        self._rescan_models()
        self._profiles_loaded=True
        self._profile_selected()

    def _profile_selected(self,*args):
        if self._loading: return
        profile_id=self.profile_combo.currentData()
        if isinstance(profile_id,str) and profile_id.startswith('preset:'):
            self._ready_selected(profile_id.split(':',1)[1]); return
        profile=next((p for p in self.snapshot['profiles'] if p['id']==profile_id),None)
        self._active_profile=profile_id
        values=profile['values'] if profile else self.snapshot['core']
        if profile:
            try: self.configuration.select_profile(profile_id)
            except (ValueError,OSError) as exc: self._show_error(getattr(exc,'code','CONFIG_WRITE_FAILED'))
        self._load_values(values,profile['name'] if profile else self.t('Моя модель','My model'))
        self._describe_profile(profile['name'] if profile else '')

    @staticmethod
    def _preset_names(item):
        from windows_ui.translations import translate
        return {translate(lang,*item['name']).casefold() for lang,_ in LANGUAGES}

    def _describe_profile(self,name):
        from server.ego_core.presets import MODEL_PRESETS
        item=next((i for i in MODEL_PRESETS if str(name).casefold() in self._preset_names(i)),None)
        self.profile_info.setText(self.t(*item['description']) if item else '')

    def _ready_info(self,item,path):
        text=self.t(*item['description'])
        if path:
            text+=self.t(' Файл найден. Нажмите «Сохранить», затем «Применить».',' File found. Click Save, then Apply.')
        else:
            text+=self.t(' Файл модели на этом ПК не найден: скачайте GGUF',' The model file is not on this PC: download the GGUF')
            if item.get('download'): text+=f' (<a href="{item["download"]}">Hugging Face</a>)'
            text+=self.t(' и укажите его кнопкой «Выбрать файл…».',' and pick it with «Choose file…».')
        return text

    def _ready_selected(self,preset_id):
        """A ready-made model: tested settings + the model file found on this PC."""
        from server.ego_core.config import CoreConfig
        from server.ego_core.presets import model_preset
        from windows_ui.model_library import match_preset
        item=model_preset(preset_id); name=self.t(*item['name'])
        base={**CoreConfig().model_dump(),**(self.snapshot.get('core') or {})}
        path=match_preset(self._model_rows,item['files'])
        values={**base,**item['values'],'model':path}
        self._active_profile=None
        self._load_values(values,name)
        if not path: self.model_fields['model'].setText('')
        self._dirty=True
        text=self._ready_info(item,path)
        self.profile_info.setText(text)
        self.model_notice.setText(self.t('Новый профиль из готовой модели — ещё не сохранён.','New profile from a ready-made model — not saved yet.'))

    def _load_values(self,values,name):
        self._form_base=dict(values)
        self._loading=True; self.profile_name.setText(self._display_name(name))
        for key,field in self.model_fields.items():
            value=values.get(key)
            try:
                if isinstance(field,QLineEdit):
                    if key in ('binary','model'):
                        value = self._preferred_model_path(key, value)
                    field.setText(value if isinstance(value,str) else '')
                elif isinstance(field,QComboBox): field.setCurrentIndex(0 if value is None else 1 if value else 2)
                else: field.setValue(value if isinstance(value,(int,float)) else 0)
            except (TypeError,OverflowError): pass
        self._loading=False; self._dirty=False; self.model_notice.setText('')
        self._sync_model_choice()

    def _preset_changed(self,*args):
        from server.ego_core.presets import preset, MEASUREMENTS
        preset_id=self.preset_combo.currentData()
        if preset_id is None: self.preset_info.setText(''); return
        item=preset(preset_id); m=MEASUREMENTS.get(preset_id)
        text=self.t(*item['description'])
        if m:
            text+=' '+self.t('Замер (8B Q8, 12 ГБ видеопамяти): {tps} ток/с, загрузка {load} с.',
                             'Measured (8B Q8, 12 GB VRAM): {tps} tok/s, load {load} s.').format(tps=m['decode_tokens_per_second'],load=m['load_seconds'])
        self.preset_info.setText(text)

    def fill_from_preset(self):
        from server.ego_core.presets import apply_preset, PRESET_FIELDS
        try: values=apply_preset(self._values(),self.preset_combo.currentData())
        except ValueError: self._show_error('PROFILE_INVALID'); return
        self._loading=True
        for key in PRESET_FIELDS:
            field=self.model_fields[key]; value=values[key]
            if isinstance(field,QComboBox): field.setCurrentIndex(0 if value is None else 1 if value else 2)
            else: field.setValue(0 if value is None else value)
        self._loading=False; self._dirty=True
        self.model_notice.setText(self.t('Значения пресета подставлены. Сохраните и примените профиль; модель перезагрузится.',
                                         'Preset values filled in. Save and apply the profile; the model will reload.'))

    def new_profile(self):
        from server.ego_core.config import CoreConfig
        from server.ego_core.presets import apply_preset, DEFAULT_PRESET_ID
        # New profiles start from the measured recommended preset, not the
        # backward-compatibility defaults (2048 ctx / 128 tokens / greedy).
        values=apply_preset(CoreConfig().model_dump(),DEFAULT_PRESET_ID)
        self._active_profile=None; self.profile_combo.setCurrentIndex(-1); self._load_values(values,self.t('Новая модель','New model')); self._dirty=True

    def save_profile(self):
        try:
            target=self._active_profile
            if target is None:
                same=next((p for p in self.configuration.snapshot()['profiles'] if p['name'].casefold()==self.profile_name.text().strip().casefold()),None)
                target=same['id'] if same else None
            profile=self.configuration.save_profile(self.profile_name.text(),self._values(),target)
            self.configuration.select_profile(profile['id']); self._active_profile=profile['id']; self._remember_model_paths(); self.reload_profiles()
            self.model_notice.setText(self.t('Профиль сохранён. Для запуска нажмите «Применить».','Profile saved. Choose Apply before starting.'))
            return profile
        except (ValueError,OSError) as exc:
            self._show_error(getattr(exc,'code','CONFIG_WRITE_FAILED')); return None

    def remove_profile(self):
        if not self._active_profile: return
        if QMessageBox.question(self,self.t('Убрать профиль','Remove profile'),self.t('Убрать профиль из списка? Файл модели останется на диске.','Remove this profile from the list? The model file will remain on disk.'))!=QMessageBox.StandardButton.Yes: return
        try: self.configuration.remove_profile(self._active_profile); self._active_profile=None; self.reload_profiles()
        except (ValueError,OSError) as exc: self._show_error(getattr(exc,'code','PROFILE_INVALID'))

    def apply_profile(self):
        if self._dirty: self._show_error('UNSAVED_CHANGES'); return
        if not self._active_profile: self._show_error('PROFILE_NOT_FOUND'); return
        restart=False
        profile=next((row for row in self.configuration.snapshot()['profiles'] if row['id']==self._active_profile),None)
        if self._online and profile is not None and self._core_endpoint_changes(profile['values']):
            if QMessageBox.question(self,self.t('Применить настройки','Apply settings'),self.t('Применить профиль? Параметры генерации сохранят модель в памяти; параметры загрузки перезапустят Runtime, сетевые параметры — Core. Сначала остановите активную генерацию.','Apply profile? Generation options keep the model loaded; loading options restart Runtime and network options restart Core. Stop any active generation first.'))!=QMessageBox.StandardButton.Yes: return
            restart=True
        self.run_action('apply_profile',{'id':self._active_profile,'restart':restart})

    def restore_working(self):
        try: values=self.configuration.working_config()
        except (ValueError,OSError) as exc: self._show_error(getattr(exc,'code','CONFIG_INVALID')); return
        restart=self._online and values is not None and self._core_endpoint_changes(values)
        if restart and QMessageBox.question(self,self.t('Восстановить','Restore'),self.t('Остановить генерацию и восстановить последнюю проверенную конфигурацию?','Stop generation and restore the last verified configuration?'))!=QMessageBox.StandardButton.Yes: return
        self.run_action('restore_working',{'restart':restart})

    def _core_endpoint_changes(self,values):
        current=self.configuration.snapshot()['core']
        return any(current.get(key)!=values.get(key) for key in ('host','api_port','local_http'))

    def check_runtime_model(self):
        """Read model metadata using the same authenticated channel as the GUI."""
        from server.ego_core import control
        self.runtime_api_info.setText(self.t('Чтение состояния Runtime…','Reading runtime status…'))
        def operation(progress,cancel):
            try:
                data=control.request(self.root,'GET','/v1/runtime/model',timeout=3)
                if not isinstance(data,dict): return {'status':'FAIL','code':'CORE_RESPONSE_INVALID'}
                return {'status':'PASS','code':'RUNTIME_MODEL_INFO','model':{k:data.get(k) for k in ('state','name','configured','ready','busy','context','max_tokens')}}
            except Exception as exc:
                return {'status':'FAIL','code':getattr(exc,'code','CORE_REQUEST_FAILED')}
        def finished(result):
            if result.get('status')=='PASS':
                data=result['model']
                self.runtime_api_info.setText(f"{data.get('name') or '—'} · {data.get('state') or 'UNKNOWN'} · ready={data.get('ready')} · context={data.get('context') or '—'}")
            else:
                self.runtime_api_info.setText(self.t('Не удалось прочитать Runtime: ','Unable to read Runtime: ')+str(result.get('code')))
        self._submit(operation,finished)

    def validate_model(self):
        values=self._values()
        def check(progress,cancel):
            from server.ego_core.config import CoreConfig
            from runtime.adapters.capabilities import probe_binary
            try: config=CoreConfig.model_validate(values); config.validate_files()
            except ValueError as exc:
                code=str(exc) if str(exc) in {'MODEL_NOT_PROVIDED','RUNTIME_NOT_PROVIDED','INVALID_GGUF'} else 'CONFIG_INVALID'
                return {'status':'FAIL','code':code}
            return probe_binary(Path(config.binary))
        self.run_operation(check,'validate_model')

    def import_profile(self):
        path,_=QFileDialog.getOpenFileName(self,self.t('Импорт профиля','Import profile'),'','JSON (*.json)')
        if not path: return
        try:
            profile=self.configuration.import_profile(Path(path)); self._active_profile=profile['id']; self.reload_profiles()
        except (ValueError,OSError) as exc: self._show_error(getattr(exc,'code','PROFILE_IMPORT_FAILED'))

    def export_profile(self):
        if not self._active_profile: self._show_error('PROFILE_NOT_FOUND'); return
        path,_=QFileDialog.getSaveFileName(self,self.t('Экспорт профиля','Export profile'),'EGO-model-profile.json','JSON (*.json)')
        if not path: return
        try: self.configuration.export_profile(self._active_profile,Path(path))
        except (ValueError,OSError) as exc: self._show_error(getattr(exc,'code','PROFILE_EXPORT_FAILED'))

    def save_preferences(self):
        try:
            gui=self.configuration.save_gui({'language':LANGUAGES[max(0,self.language_combo.currentIndex())][0],'theme':'dark' if self.theme_combo.currentIndex()==0 else 'light','reduced_effects':self.reduced_effects.isChecked(),'close_behavior':self.close_combo.currentData()})
            self.lang=gui['language']; self.setStyleSheet(stylesheet(gui['theme'])); self._retranslate()
        except (ValueError,OSError) as exc: self._show_error(getattr(exc,'code','GUI_INVALID'))

    def _submit(self,operation,finished,progress=None):
        task=Task(operation); self._tasks.add(task)
        if progress: task.signals.progress.connect(progress)
        def done(result):
            self._tasks.discard(task); finished(result)
        task.signals.finished.connect(done); self.pool.start(task); return task

    def run_action(self,action,payload=None):
        self.run_operation(lambda progress,cancel:self.backend.execute(action,payload=payload,progress=progress,cancel=cancel),action)

    def run_operation(self,operation,name):
        if self.busy: return False
        self.busy=True; self._action_name=name; self._started=time.monotonic(); self._stage='VALIDATING'; self._stage_started=time.monotonic()
        self._stage_records=[]; self.result_text.clear(); self.result_label.setText(self.t('Выполняется: ','Running: ')+name)
        self.progress_bar.setRange(0,0); self.cancel_button.setEnabled(True)
        for button in self.action_buttons: button.setEnabled(False)
        self.elapsed_timer.start(); self._action=self._submit(operation,self._action_finished,self._progress)
        self._elapsed(); return True

    def _progress(self,event):
        stage=event.get('stage','VALIDATING')
        if stage!=self._stage:
            self._stage_records.append({'stage':self._stage,'duration_seconds':round(time.monotonic()-self._stage_started,3)})
            self._stage=stage; self._stage_started=time.monotonic()
        self.result_text.appendPlainText(str(event.get('started_at',''))+'  '+stage+'  '+str(event.get('message','')))
        self._elapsed()

    def _elapsed(self):
        if self.busy:
            text=f'{self._action_name} · {self._stage} · {time.monotonic()-self._started:.1f} s'
            self.stage_label.setText(text); self.statusBar().showMessage(text)

    def cancel_task(self):
        if self._action:
            self._action.cancel.set(); self.cancel_button.setEnabled(False)
            self.result_label.setText(self.t('Отмена запрошена; ожидается завершение принадлежащей задаче операции.','Cancellation requested; waiting for the owned operation to finish.'))

    def _action_finished(self,result):
        self.busy=False; self._action=None; self.elapsed_timer.stop(); self.progress_bar.setRange(0,1); self.progress_bar.setValue(1 if result.get('status')=='PASS' else 0); self.cancel_button.setEnabled(False)
        for button in self.action_buttons: button.setEnabled(True)
        self.result_label.setText(f"{result.get('status','FAIL')} · {result.get('code','GUI_RESULT_INVALID')} · {result.get('duration_seconds',round(time.monotonic()-getattr(self,'_started',time.monotonic()),2))} s")
        self.statusBar().showMessage(self.result_label.text())
        self.result_text.appendPlainText(json.dumps(result,ensure_ascii=False,indent=2,default=str))
        self.stage_label.setText({'PASS':'COMPLETED','CANCELLED':'CANCELLED'}.get(result.get('status'),'FAILED'))
        if result.get('status') not in {'PASS','SKIPPED','CANCELLED'}: self._show_error(result.get('cause_code') or result.get('code','GUI_OPERATION_FAILED'),result)
        elif result.get('status')=='PASS': self.error_label.setText('—')
        warnings=result.get('preflight') if isinstance(result.get('preflight'),list) else []
        if warnings:
            # Stage 4d: Core runs, but the setup has issues (no model, busy model port, VRAM...).
            text='\n'.join(self.t(w.get('message',''),w.get('code',''))+' '+w.get('hint','') for w in warnings[:3])
            self.model_notice.setText(text); self.error_label.setText(text)
        self.refresh_history()
        if getattr(self,'_action_name','') in {'apply_profile','restore_working'}: self.reload_profiles()
        callback=getattr(self,'_after_action',None); self._after_action=None
        if callback: callback(result)
        if self._stop_close:
            if getattr(self,'_action_name','') == 'stop':
                self._stop_close=False
                if result.get('status')=='PASS':
                    self._allow_close=True
                    self.close()
                    QApplication.instance().quit()
                else:
                    self._shutdown_warning=QMessageBox(QMessageBox.Icon.Warning, 'EGO', self.t(
                        'Core не подтвердил остановку. Окно останется открытым. Вы можете выйти отдельно через меню закрытия, оставив Core работающим.',
                        'Core did not confirm shutdown. You can close the GUI separately, leaving Core running.'),QMessageBox.StandardButton.Ok,self)
                    self._shutdown_warning.open()
            else:
                QTimer.singleShot(0,lambda:self.run_action('stop'))
        self.poll()

    def _show_error(self,code,result=None):
        # Stage 4b: a readable message from the shared catalog, the code kept for diagnostics.
        from server.ego_core.errors import describe
        code=str(code); (ru,ru_hint),(en,en_hint)=describe(code,'ru'),describe(code,'en')
        helptext=self.t(*ERROR_HELP[code]) if code in ERROR_HELP else self.t(ru_hint,en_hint)
        numbers=' '.join(f'{key}={result[key]}' for key in ('errno','winerror','exit_code') if result and type(result.get(key)) is int)
        text=self.t(ru,en)+' ('+code+(' · '+numbers if numbers else '')+')\n'+helptext
        self.error_label.setText(text); self.model_notice.setText(text)
        self.statusBar().showMessage(text.replace('\n',' · '))

    def poll(self):
        if self._polling: return
        self._polling=True
        def work(progress,cancel):
            return {'status_result':self.backend.execute('status',cancel=cancel),'monitor_result':self.backend.execute('monitor',cancel=cancel)}
        self._submit(work,self._polled)

    def _polled(self,result):
        self._polling=False
        status=result.get('status_result',{}); core=status.get('core',{}); state=core.get('state')
        self._online=status.get('status')=='PASS' and state not in {'OFFLINE','LOCKED','UNINITIALIZED'} and bool(core)
        self.connection_label.setText('API · '+self.t('подключён','connected') if self._online else 'API · '+self.t('нет соединения','offline'))
        self.dashboard_values['core'].setText(self.t('Работает','Running') if self._online else str(state or status.get('code',self.t('Неизвестно','Unknown'))))
        runtime_state=core.get('runtime_state') if self._online else None
        if runtime_state not in {'STOPPED','STARTING','LOADING','READY','BUSY','STOPPING','ERROR'}: runtime_state='UNKNOWN'
        runtime_pid=core.get('runtime_pid') if self._online else None
        if type(runtime_pid) is not int or runtime_pid<=0: runtime_pid=None
        runtime_text=runtime_state
        if runtime_pid is not None: runtime_text+=' · PID '+str(runtime_pid)
        if self._online and type(core.get('model_ready')) is bool:
            runtime_text+=' · '+(self.t('модель готова','model ready') if core['model_ready'] else self.t('модель не готова','model not ready'))
        self.dashboard_values['runtime'].setText(runtime_text)
        snap=self.configuration.snapshot(); model=snap['core'].get('model'); self.dashboard_values['model'].setText(Path(model).name if isinstance(model,str) else self.t('Не настроена','Not configured'))
        uptime=core.get('uptime_seconds'); self.uptime_label.setText('Uptime · '+(f'{uptime:.0f} s' if isinstance(uptime,(int,float)) else self.t('Неизвестно','Unknown')))
        error=core.get('last_runtime_error') or core.get('last_api_error')
        if isinstance(error,dict): self._show_error(error.get('code','RUNTIME_INTERNAL_ERROR'),error)
        elif error: self._show_error(error)
        data=result.get('monitor_result',{}).get('details',{}); unknown=self.t('Неизвестно','Unknown')
        # Core reports actual llama.cpp completion timings, never inferred from text length.
        generation_metrics=core.get('generation_metrics') or {}
        if isinstance(core.get('model_load_seconds'),(int,float)):
            data['model_load_seconds']=core['model_load_seconds']
        if isinstance(generation_metrics.get('tokens_per_second'),(int,float)):
            data['tokens_per_second']=generation_metrics['tokens_per_second']
        elif isinstance(generation_metrics.get('average_request_tokens_per_second'),(int,float)):
            data['tokens_per_second']=generation_metrics['average_request_tokens_per_second']
            data['speed_is_average']=True
        prompt_tokens=generation_metrics.get('prompt_tokens')
        context_limit=core.get('runtime_context_limit')
        if type(prompt_tokens) is int and type(context_limit) is int and context_limit>0:
            data['context_used']=f'{prompt_tokens} / {context_limit} ({prompt_tokens/context_limit:.0%}, входной запрос; не KV-cache)'
        if generation_metrics.get('generation_seconds') is None and isinstance(generation_metrics.get('request_seconds'),(float,int)):
            data['generation_seconds']=generation_metrics['request_seconds']
        for field in ('prompt_tokens','completion_tokens','generation_seconds','finish_reason'):
            if generation_metrics.get(field) is not None:
                data[field]=generation_metrics[field]
        data['queued_generations']=len(core.get('queued_generations') or [])
        def numeric(key,suffix=''):
            value=data.get(key); return f'{value:.1f}{suffix}' if isinstance(value,(int,float)) else unknown
        ram=(f"{data['ram_used_bytes']/1024**3:.1f} / {data['ram_total_bytes']/1024**3:.1f} GiB" if isinstance(data.get('ram_used_bytes'),(int,float)) and isinstance(data.get('ram_total_bytes'),(int,float)) else unknown)
        vram=(f"{data['vram_used_mib']:.0f} / {data['vram_total_mib']:.0f} MiB" if isinstance(data.get('vram_used_mib'),(int,float)) and isinstance(data.get('vram_total_mib'),(int,float)) else unknown)
        self.dashboard_values['cpu'].setText(numeric('cpu_percent',' %')); self.dashboard_values['ram'].setText(ram); self.dashboard_values['gpu'].setText(vram)
        for key,widget in self.monitor_values.items():
            if key=='ram': text=ram
            elif key=='vram': text=vram
            elif key=='context_used' and isinstance(data.get('context_used'),str): text=data['context_used']
            elif key=='tokens_per_second' and data.get('speed_is_average') and isinstance(data.get(key),(int,float)):
                text=f"~{data[key]:.2f} (средняя за запрос)"
            elif key in {'prompt_tokens','completion_tokens','queued_generations'} and type(data.get(key)) is int: text=str(data[key])
            elif key=='finish_reason' and isinstance(data.get(key),str): text=data[key]
            elif key=='runtime_listener':
                listener=data.get(key,{}) or {}
                if runtime_pid is not None:
                    text='PID '+str(runtime_pid)+' · '+self.t('принадлежит Core','owned by Core')+' · '+runtime_state
                    if listener.get('pid')==runtime_pid:
                        rss=data.get('runtime_memory_mib'); uptime=data.get('runtime_uptime_seconds')
                        if isinstance(rss,(int,float)): text+=f' · RAM {rss:.0f} MiB'
                        if isinstance(uptime,(int,float)): text+=f' · {uptime:.0f} s'
                elif listener.get('observed'):
                    text='PID '+str(listener.get('pid') or '?')+' · '+self.t('наблюдение','observed')
                    if data.get('runtime_executable')=='llama-server.exe':
                        rss=data.get('runtime_memory_mib'); uptime=data.get('runtime_uptime_seconds')
                        if isinstance(rss,(int,float)): text+=f' · RAM {rss:.0f} MiB'
                        if isinstance(uptime,(int,float)): text+=f' · {uptime:.0f} s'
                else: text='STOPPED' if runtime_state=='STOPPED' else unknown
            else: text=numeric(key,' %' if key.endswith('percent') else ' °C' if key.endswith('_c') else '')
            widget.setText(text)
        errors=data.get('errors',[]); self.monitor_notice.setText('\n'.join(map(str,errors)))

    def refresh_history(self):
        try: rows=self.backend.history()[-30:][::-1]
        except Exception: rows=[]
        self.history_table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,key in enumerate(('started_at','action','status','duration_seconds')): self.history_table.setItem(i,j,QTableWidgetItem(str(row.get(key,''))))

    def refresh_logs(self):
        level=self.log_level.currentText(); search=self.log_search.text()
        def operation(progress,cancel): return {'status':'PASS','code':'LOGS_LOADED','rows':self.backend.logs(level='' if level=='ALL' else level,search=search)}
        def done(result):
            self.log_text.setPlainText('\n'.join(f"[{row.get('level','INFO')}] {row.get('file','')}\n{row.get('text','')}" for row in result.get('rows',[])))
        self._submit(operation,done)

    def open_local(self,path):
        path=Path(path)
        # Opening is restricted to this project's results, never arbitrary user input.
        if path==self.root/'logs' and path.is_dir(): QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def export_diagnostics(self):
        path,_=QFileDialog.getSaveFileName(self,self.t('Экспорт диагностики','Export diagnostics'),'EGO-diagnostics.zip','ZIP (*.zip)')
        if not path: return
        def operation(progress,cancel):
            file=self.backend.export_diagnostics(Path(path)); return {'status':'PASS','code':'DIAGNOSTICS_EXPORTED','file':str(file)}
        self.run_operation(operation,'export_diagnostics')

    def clean_logs(self):
        if QMessageBox.question(self,self.t('Очистка логов','Clear logs'),self.t('Удалить разрешённые диагностические логи старше 30 дней? Данные и конфигурации сохранятся.','Delete approved diagnostic logs older than 30 days? Data and configuration will be preserved.'))!=QMessageBox.StandardButton.Yes: return
        self.run_operation(lambda progress,cancel:{'status':'PASS','code':'LOGS_CLEANED','count':self.backend.clean_logs(30,confirmed=True)},'clean_logs')

    def _tray(self):
        self.tray=QSystemTrayIcon(self.windowIcon(),self); menu=QMenu(self)
        show=QAction(self.t('Открыть EGO','Open EGO'),self); show.triggered.connect(self.activate); menu.addAction(show)
        stop=QAction(self.t('Остановить Core и закрыть','Stop Core and quit'),self); stop.triggered.connect(self.stop_and_close); menu.addAction(stop)
        force=QAction(self.t('Принудительно закрыть EGO','Force close EGO'),self); force.triggered.connect(self.force_close); menu.addAction(force)
        self.tray.setContextMenu(menu); self.tray.activated.connect(lambda reason:self.activate() if reason==QSystemTrayIcon.ActivationReason.DoubleClick else None)
        if QSystemTrayIcon.isSystemTrayAvailable(): self.tray.show()

    def activate(self): self.showNormal(); self.raise_(); self.activateWindow()

    def force_close(self):
        if QMessageBox.question(self,'EGO',self.t(
                'Принудительно закрыть EGO? Все активные генерации прервутся. Core, Runtime и окно будут завершены.',
                'Force close EGO? Active generations will stop. Core, Runtime and GUI will exit.'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        from server.ego_core.control import FROZEN, CORE_EXE
        command=([str(Path(sys.executable).with_name(CORE_EXE)),'force-close','--root',str(self.root)] if FROZEN
                 else [sys.executable,str(self.root/'scripts/force_close.py'),'--root',str(self.root)])
        try:
            # Detached supervisor survives GUI exit; script targets only this
            # installation's verified Core process and its descendants.
            kwargs={'cwd':str(self.root),'stdin':subprocess.DEVNULL,
                    'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL}
            if os.name=='nt':
                kwargs['creationflags']=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
            else: kwargs['start_new_session']=True
            subprocess.Popen(command,**kwargs)
        except OSError:
            QMessageBox.warning(self,'EGO',self.t('Не удалось запустить аварийную остановку.','Could not launch emergency shutdown.'))
            return
        self._stop_close=False
        self._allow_close=True
        self.close()
        QApplication.instance().quit()

    def stop_and_close(self):
        self._stop_close=True
        wizard=getattr(self,'wizard',None)
        if wizard is not None:
            # Closing the main window must also revoke the wizard's queued
            # initialize/start/smoke steps before waiting for its current task.
            wizard.reject()
        if self.busy:
            # Cancel the current operation first; after it reports completion,
            # _action_finished schedules the stop on the GUI event loop.
            self.cancel_task()
            self.statusBar().showMessage(self.t('Завершаем операцию перед остановкой Core…','Cancelling operation before stopping Core…'))
            return
        self.run_action('stop')

    def closeEvent(self,event:QCloseEvent):
        if self._allow_close:
            self.poll_timer.stop(); self.tray.hide()
            for task in self._tasks: task.cancel.set()
            event.accept(); return
        behavior=self.configuration.snapshot()['gui']['close_behavior']
        if behavior=='ask':
            box=QMessageBox(self); box.setWindowTitle('EGO'); box.setText(self.t('Что сделать с сервером?','What should happen to the server?'))
            hide=box.addButton(self.t('Скрыть, оставить Core','Hide, leave Core running'),QMessageBox.ButtonRole.AcceptRole)
            stop=box.addButton(self.t('Остановить и закрыть','Stop and close'),QMessageBox.ButtonRole.DestructiveRole)
            force=box.addButton(self.t('Принудительно закрыть EGO','Force close EGO'),QMessageBox.ButtonRole.DestructiveRole)
            leave=box.addButton(self.t('Закрыть GUI, оставить Core','Quit GUI, leave Core'),QMessageBox.ButtonRole.ActionRole)
            box.addButton(self.t('Отмена','Cancel'),QMessageBox.ButtonRole.RejectRole); box.exec()
            behavior='hide' if box.clickedButton() is hide else 'stop' if box.clickedButton() is stop else 'force' if box.clickedButton() is force else 'leave' if box.clickedButton() is leave else 'cancel'
        event.ignore()
        if behavior=='hide': self.hide()
        elif behavior=='stop': self.stop_and_close()
        elif behavior=='force': self.force_close()
        elif behavior=='leave':
            self._allow_close=True
            self.poll_timer.stop()
            for task in self._tasks: task.cancel.set()
            self.close()
            QApplication.instance().quit()

    def show_setup(self):
        if getattr(self,'wizard',None) is not None and self.wizard.isVisible(): self.wizard.raise_(); return
        self.wizard=SetupWizard(self); self.wizard.show()


class SetupWizard(QWizard):
    """Reopenable guided setup. No downloads or encrypted-data reset actions."""
    def __init__(self,window):
        super().__init__(window)
        self._abandoned=False; self._running_action=False; self._queue=[]
        self.rejected.connect(self._abandon)
        self.window=window; self.setWindowTitle(window.t('EGO · Первоначальная настройка','EGO · Initial setup')); self.resize(790,590)
        # Windows native wizard pages can paint opaque system-white panels even
        # when the parent main window has a dark QSS. Explicitly style the wizard
        # and its pages, and provide a matching Qt palette for native subcontrols.
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        dark = window.snapshot['gui']['theme'] == 'dark'
        bg = '#0c101c' if dark else '#edf1f8'
        panel = '#151d30' if dark else '#ffffff'
        fg = '#eef3ff' if dark else '#15203b'
        muted = '#9aaac6' if dark else '#526481'
        palette = QPalette(self.palette())
        for role, color in ((QPalette.ColorRole.Window,bg),
                            (QPalette.ColorRole.Base,panel),
                            (QPalette.ColorRole.Button,panel),
                            (QPalette.ColorRole.WindowText,fg),
                            (QPalette.ColorRole.Text,fg),
                            (QPalette.ColorRole.ButtonText,fg),
                            (QPalette.ColorRole.PlaceholderText,muted)):
            palette.setColor(role, QColor(color))
        self.setPalette(palette)
        self.setStyleSheet(stylesheet(window.snapshot['gui']['theme']) + f"""
            QWizard, QWizardPage {{ background-color: {bg}; color: {fg}; }}
            QWizard QLabel {{ background: transparent; color: {fg}; }}
            QWizard QPlainTextEdit {{ background-color: {panel}; color: {fg}; }}
            QWizard QPushButton {{ color: {fg}; }}
        """)
        self.setButtonText(QWizard.WizardButton.BackButton, window.t('Назад','Back'))
        self.setButtonText(QWizard.WizardButton.NextButton, window.t('Далее','Next'))
        self.setButtonText(QWizard.WizardButton.CancelButton, window.t('Отмена','Cancel'))
        self.setButtonText(QWizard.WizardButton.FinishButton, window.t('Готово','Finish'))
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage)
        env=QWizardPage(); env.setTitle(window.t('1. Проверка окружения','1. Environment check')); layout=QVBoxLayout(env)
        layout.addWidget(QLabel(window.t('Проверка Python, Windows, шифрования и видеокарты.','Check Python, Windows, encryption and the GPU.')))
        self.summary=QPlainTextEdit(); self.summary.setReadOnly(True); layout.addWidget(self.summary)
        check=QPushButton(window.t('Проверить окружение','Check environment')); check.clicked.connect(self.environment); layout.addWidget(check); self.addPage(env)
        models=QWizardPage(); models.setTitle(window.t('2. Выбор файлов модели','2. Choose model files')); form=QFormLayout(models)
        self.binary=QLineEdit(window.model_fields['binary'].text()); self.model=QLineEdit(window.model_fields['model'].text())
        form.addRow('llama-server',window._path_row(self.binary)); form.addRow('GGUF',window._path_row(self.model,True))
        self.layers=QSpinBox(); self.layers.setRange(0,999); self.layers.setValue(window.model_fields['gpu_layers'].value()); form.addRow('GPU layers',self.layers)
        note=QLabel(window.t('Модель не скачивается автоматически. Дополнительные параметры доступны во вкладке «Запуск». Сохранение профиля не изменяет базы данных.','Models are never downloaded automatically. More options are available in the Launch tab. Saving a profile does not change databases.')); note.setWordWrap(True); form.addRow(note); self.addPage(models)
        trial=QWizardPage(); trial.setTitle(window.t('3. Сохранение и пробная генерация','3. Save and trial generation')); layout=QVBoxLayout(trial)
        note=QLabel(window.t('Проверка файлов → сохранение профиля → первый запуск сервера → пробный ответ модели. Существующие данные не удаляются.','Check files → save the profile → first server start → trial reply. Existing data is kept.')); note.setWordWrap(True); layout.addWidget(note)
        self.trial_button=QPushButton(window.t('Сохранить и проверить','Save and test')); self.trial_button.clicked.connect(self.trial); layout.addWidget(self.trial_button)
        self.report=QPlainTextEdit(); self.report.setReadOnly(True); layout.addWidget(self.report,1); self.addPage(trial)

    def environment(self):
        if self.window.busy: return
        self._running_action=True
        self.window._after_action=self._environment_finished
        self.window.run_action('environment')

    def _environment_finished(self,result):
        self._running_action=False
        if not self._abandoned: self.summary.setPlainText(json.dumps(result,ensure_ascii=False,indent=2))

    def _abandon(self):
        self._abandoned=True; self._queue=[]
        if self._running_action:
            if getattr(getattr(self.window,'_after_action',None),'__self__',None) is self:
                self.window._after_action=None
            self.window.cancel_task()
        self._running_action=False

    def trial(self):
        window=self.window
        if window.busy: return
        if window._online:
            self.report.setPlainText(window.t('Сервер уже работает. Выберите модель на странице «AI-модели» и нажмите «Применить».','The server is already running. Choose a model in the Launch tab and click Apply.')); return
        window.model_fields['binary'].setText(self.binary.text()); window.model_fields['model'].setText(self.model.text()); window.model_fields['gpu_layers'].setValue(self.layers.value())
        profile=window.save_profile()
        if not profile: self.report.setPlainText(window.error_label.text()); return
        self.trial_button.setEnabled(False); self.button(QWizard.WizardButton.FinishButton).setEnabled(False)
        self._queue=[('apply_profile',{'id':profile['id'],'restart':False}),('initialize',None),('start',None),('smoke',None)]; self.report.clear(); self._next()

    def _next(self,result=None):
        self._running_action=False
        if self._abandoned: return
        if result is not None:
            self.report.appendPlainText(json.dumps(result,ensure_ascii=False,indent=2))
            if result.get('status')!='PASS': self._queue=[]
        if not self._queue:
            self.trial_button.setEnabled(True); self.button(QWizard.WizardButton.FinishButton).setEnabled(True); return
        action,payload=self._queue.pop(0); self._running_action=True; self.window._after_action=self._next; self.window.run_action(action,payload)
