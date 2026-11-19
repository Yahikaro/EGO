"""Lightweight Qt styling; no blur, transparency or GPU-heavy effects required."""
def stylesheet(theme='dark'):
    dark = theme == 'dark'
    bg, panel, field, fg, muted, border = (
        ('#0c101c','#151d30','#0f1627','#eef3ff','#9aaac6','#293752') if dark else
        ('#edf1f8','#ffffff','#f4f6fc','#15203b','#526481','#d2dbeb'))
    return f'''
        QWidget {{ color:{fg}; font-family:"Segoe UI", "DejaVu Sans"; font-size:14px; }}
        QMainWindow, QDialog, QWizard {{ background:{bg}; }}
        QWidget#sidebar {{ background:{panel}; border-right:1px solid {border}; }}
        QFrame#card {{ background:{panel}; border:1px solid {border}; border-radius:14px; }}
        QLabel#brand {{ font-size:30px; font-weight:700; color:#80baff; }}
        QLabel#heading {{ font-size:25px; font-weight:600; }}
        QLabel#muted {{ color:{muted}; }}
        QLabel#value {{ font-size:19px; font-weight:600; }}
        QListWidget {{ background:transparent; border:none; outline:none; }}
        QListWidget::item {{ padding:13px 12px; border-radius:9px; margin:3px; }}
        QListWidget::item:selected {{ background:#245ca2; color:white; }}
        QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox,QPlainTextEdit,QTextBrowser,QTableWidget {{
            background:{field}; border:1px solid {border}; border-radius:7px; padding:6px;
            selection-background-color:#276ac1; }}
        QComboBox QAbstractItemView {{ background:{panel}; selection-background-color:#276ac1; }}
        QPushButton {{ background:{panel}; border:1px solid {border}; border-radius:8px; padding:9px 14px; }}
        QPushButton:hover {{ border-color:#69a9ff; background:#24518a; color:white; }}
        QPushButton#primary {{ background:#286cc5; border-color:#4388e1; color:white; font-weight:600; }}
        QPushButton:disabled {{ color:{muted}; background:{field}; }}
        QPushButton:pressed {{ background:#173d71; padding-top:10px; padding-bottom:8px; }}
        QPushButton:focus, QLineEdit:focus, QPlainTextEdit:focus, QTextBrowser:focus {{ border:1px solid #5b9de9; }}
        QToolButton#disclosure {{ text-align:left; background:{panel}; border:1px solid {border}; border-radius:9px; padding:10px 12px; color:{fg}; font-weight:600; }}
        QToolButton#disclosure:hover {{ background:#203656; border-color:#5e99dc; }}
        QTextBrowser {{ background:{field}; border:1px solid {border}; border-radius:10px; padding:12px; selection-background-color:#276ac1; }}
        QGroupBox {{ border:1px solid {border}; border-radius:10px; margin-top:12px; padding-top:20px; }}
        QGroupBox::title {{ subcontrol-origin:margin; left:12px; padding:0 5px; }}
        QScrollArea {{ background:transparent; border:none; }}
        QScrollArea > QWidget > QWidget {{ background:transparent; }}
        QScrollBar:vertical {{ background:transparent; width:7px; margin:2px; }}
        QScrollBar::handle:vertical {{ background:{border}; border-radius:3px; min-height:30px; }}
        QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{ height:0; }}
        QHeaderView::section {{ background:{panel}; padding:6px; border:1px solid {border}; }}
        QProgressBar {{ border:1px solid {border}; border-radius:5px; background:{field}; min-height:8px; }}
        QProgressBar::chunk {{ background:#4388e1; border-radius:4px; }}
        QTabWidget::pane {{ border:none; background:transparent; top:-1px; }}
        QTabBar::tab {{ background:{panel}; border:1px solid {border}; border-bottom:none; border-top-left-radius:9px; border-top-right-radius:9px; padding:9px 18px; margin:0 3px 0 0; color:{muted}; }}
        QTabBar::tab:selected {{ background:#245ca2; color:white; border-color:#245ca2; }}
        QTabBar::tab:hover:!selected {{ color:{fg}; border-color:#5e99dc; }}
        QToolTip {{ background:{panel}; color:{fg}; border:1px solid {border}; padding:5px; }}
    '''
