"""The harness window: stable, accessible controls with deterministic effects.

Every interactive control carries a fixed ``objectName`` (which Qt's Windows UIA
bridge publishes as ``AutomationId``) and a fixed accessible name, so a workflow
recorded against the harness resolves by selector alone and never needs a
coordinate fallback.  Each control's only observable effect is a counter or one
of the fixed synthetic strings in :mod:`samples.test_harness.state`.

The harness deliberately imports nothing from ``universal_rpa``: it must behave
like any other third-party Windows application under automation.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QMouseEvent, QShortcut, QWheelEvent
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from samples.test_harness.state import (
    SYNTHETIC_KOREAN,
    HarnessStateFile,
    synthetic_table_text,
)

#: Every control's AutomationId, as a workflow recorded on the harness sees it.
NORMAL_TEXT_ID = "normalText"
DATE_TEXT_ID = "dateText"
KOREAN_TEXT_ID = "koreanText"
PASSWORD_TEXT_ID = "passwordText"
CLICK_BUTTON_ID = "clickButton"
DOUBLE_CLICK_BUTTON_ID = "doubleClickButton"
DRAG_SURFACE_ID = "dragSurface"
SCROLL_SURFACE_ID = "scrollSurface"
HOTKEY_INDICATOR_ID = "hotkeyIndicator"
DELAYED_CONTROL_ID = "delayedControl"
OPEN_MODAL_BUTTON_ID = "openModalButton"
MODAL_CLOSE_BUTTON_ID = "modalCloseButton"
DUPLICATE_BUTTON_ID = "duplicateButton"
COPY_TABLE_BUTTON_ID = "copyTableButton"


def _identify(widget: QWidget, automation_id: str, accessible_name: str) -> None:
    """Give one widget the stable identity a recorded selector will match."""

    widget.setObjectName(automation_id)
    widget.setAccessibleName(accessible_name)


class DragSurface(QWidget):
    """Reports a completed press → move → release gesture, and nothing else."""

    dragged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(220, 60)
        self.setAutoFillBackground(True)
        self._pressed_at: QPoint | None = None
        self._moved = False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed_at = event.position().toPoint()
            self._moved = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        origin = self._pressed_at
        if origin is not None:
            delta = event.position().toPoint() - origin
            if abs(delta.x()) >= 5 or abs(delta.y()) >= 5:
                self._moved = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._pressed_at is not None:
            if self._moved:
                self.dragged.emit()
            self._pressed_at = None
            self._moved = False
        super().mouseReleaseEvent(event)


class ScrollSurface(QWidget):
    """Counts wheel notches so a scroll step has an observable effect."""

    scrolled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(220, 60)
        self.setAutoFillBackground(True)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.angleDelta().y() or event.angleDelta().x():
            self.scrolled.emit()
        event.accept()


class DoubleClickButton(QPushButton):
    """A button that reports double-clicks separately from single clicks."""

    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class OwnedModal(QDialog):
    """An application-modal dialog owned by the harness window."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("확인 대화 상자")
        self.setModal(True)
        _identify(self, "ownedModal", "확인 대화 상자")
        self.close_button = QPushButton("확인")
        _identify(self.close_button, MODAL_CLOSE_BUTTON_ID, "확인")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("모달 대화 상자입니다."))
        layout.addWidget(self.close_button)
        self.close_button.clicked.connect(self.accept)


class HarnessWindow(QMainWindow):
    """One deterministic automation target driven entirely by its config."""

    ready = Signal()

    def __init__(self, config: object, state: HarnessStateFile) -> None:
        super().__init__()
        self.config = config
        self.state_file = state
        self.setWindowTitle("Universal RPA Test Harness")
        _identify(self, "harnessMainWindow", "Universal RPA Test Harness")
        self.resize(720, 620)

        self.normal_text = QLineEdit()
        _identify(self.normal_text, NORMAL_TEXT_ID, "일반 텍스트")
        self.date_text = QLineEdit()
        _identify(self.date_text, DATE_TEXT_ID, "날짜")
        self.korean_text = QLineEdit()
        _identify(self.korean_text, KOREAN_TEXT_ID, "한글 텍스트")
        self.password_text = QLineEdit()
        self.password_text.setEchoMode(QLineEdit.EchoMode.Password)
        _identify(self.password_text, PASSWORD_TEXT_ID, "비밀번호")

        fields = QGroupBox("입력")
        field_layout = QFormLayout(fields)
        field_layout.addRow("일반 텍스트", self.normal_text)
        field_layout.addRow("날짜", self.date_text)
        field_layout.addRow("한글 텍스트", self.korean_text)
        field_layout.addRow("비밀번호", self.password_text)

        self.click_button = QPushButton("클릭")
        _identify(self.click_button, CLICK_BUTTON_ID, "클릭")
        self.double_click_button = DoubleClickButton("더블클릭")
        _identify(self.double_click_button, DOUBLE_CLICK_BUTTON_ID, "더블클릭")
        self.copy_table_button = QPushButton("표 복사")
        _identify(self.copy_table_button, COPY_TABLE_BUTTON_ID, "표 복사")
        self.open_modal_button = QPushButton("모달 열기")
        _identify(self.open_modal_button, OPEN_MODAL_BUTTON_ID, "모달 열기")

        buttons = QHBoxLayout()
        for button in (
            self.click_button,
            self.double_click_button,
            self.copy_table_button,
            self.open_modal_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)

        self.drag_surface = DragSurface()
        _identify(self.drag_surface, DRAG_SURFACE_ID, "드래그 영역")
        self.scroll_surface = ScrollSurface()
        _identify(self.scroll_surface, SCROLL_SURFACE_ID, "스크롤 영역")
        surfaces = QHBoxLayout()
        surfaces.addWidget(self.drag_surface, 1)
        surfaces.addWidget(self.scroll_surface, 1)

        self.hotkey_indicator = QLabel("단축키 0회")
        _identify(self.hotkey_indicator, HOTKEY_INDICATOR_ID, "단축키 표시")

        # Hidden until ``delayed_control_ms`` elapses, so a wait condition has
        # something real to poll for; never shown at all under intentional_timeout.
        self.delayed_control = QPushButton("지연 표시 대상")
        _identify(self.delayed_control, DELAYED_CONTROL_ID, "지연 표시 대상")
        self.delayed_control.setVisible(False)

        self.duplicate_buttons: tuple[QPushButton, ...] = ()
        duplicates = QHBoxLayout()
        if getattr(config, "duplicate_selector", False):
            first = QPushButton("중복 대상")
            second = QPushButton("중복 대상")
            for button in (first, second):
                _identify(button, DUPLICATE_BUTTON_ID, "중복 대상")
                duplicates.addWidget(button)
            duplicates.addStretch(1)
            self.duplicate_buttons = (first, second)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(fields)
        layout.addLayout(buttons)
        layout.addLayout(surfaces)
        layout.addWidget(self.hotkey_indicator)
        layout.addWidget(self.delayed_control)
        layout.addLayout(duplicates)
        layout.addStretch(1)
        self.setCentralWidget(central)

        self._modal: OwnedModal | None = None
        self._select_all = QShortcut(QKeySequence.StandardKey.SelectAll, self)
        self._select_all.setContext(Qt.ShortcutContext.WindowShortcut)
        self._select_all.activated.connect(self._on_hotkey)

        self.click_button.clicked.connect(lambda: self.state_file.bump("click_count"))
        self.double_click_button.double_clicked.connect(
            lambda: self.state_file.bump("double_click_count")
        )
        self.drag_surface.dragged.connect(lambda: self.state_file.bump("drag_count"))
        self.scroll_surface.scrolled.connect(lambda: self.state_file.bump("scroll_count"))
        self.copy_table_button.clicked.connect(self._on_copy_table)
        self.open_modal_button.clicked.connect(self._on_open_modal)
        self.normal_text.textChanged.connect(self._on_text_changed)
        self.korean_text.textChanged.connect(self._on_text_changed)
        self.date_text.textChanged.connect(self._on_text_changed)
        self.password_text.textChanged.connect(self._on_password_changed)
        for field in (self.normal_text, self.date_text, self.korean_text):
            field.returnPressed.connect(self._on_return_pressed)

        self._delay_timer = QTimer(self)
        self._delay_timer.setSingleShot(True)
        self._delay_timer.timeout.connect(self._reveal_delayed_control)

    def start(self) -> None:
        """Arm the delayed control and announce readiness once shown."""

        if not getattr(self.config, "intentional_timeout", False):
            self._delay_timer.start(max(0, int(getattr(self.config, "delayed_control_ms", 500))))
        QTimer.singleShot(0, self.ready.emit)

    def seed_korean_text(self) -> None:
        """Preload the fixed Korean string a verification assertion compares."""

        self.korean_text.setText(SYNTHETIC_KOREAN)

    def _reveal_delayed_control(self) -> None:
        self.delayed_control.setVisible(True)
        self.state_file.update(delayed_control_visible=True)

    def _on_hotkey(self) -> None:
        focused = QGuiApplication.focusObject()
        if isinstance(focused, QLineEdit):
            focused.selectAll()
        state = self.state_file.bump("hotkey_count")
        self.hotkey_indicator.setText(f"단축키 {state.hotkey_count}회")

    def _on_text_changed(self) -> None:
        self.state_file.publish(
            replace(
                self.state_file.state,
                set_text_count=self.state_file.state.set_text_count + 1,
                normal_text=self.normal_text.text(),
                date_text=self.date_text.text(),
                korean_text=self.korean_text.text(),
            )
        )

    def _on_password_changed(self) -> None:
        # Only presence is recorded. The password's characters never leave the field.
        self.state_file.update(password_present=bool(self.password_text.text()))

    def _on_return_pressed(self) -> None:
        self.state_file.bump("press_key_count")

    def _on_copy_table(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(synthetic_table_text())
        self.state_file.bump("copy_table_count")

    def _on_open_modal(self) -> None:
        modal = OwnedModal(self)
        self._modal = modal
        self.state_file.bump("modal_open_count")
        modal.finished.connect(lambda _: self.state_file.bump("modal_close_count"))
        modal.show()

    def event(self, event: QEvent) -> bool:
        return super().event(event)


__all__ = [
    "CLICK_BUTTON_ID",
    "COPY_TABLE_BUTTON_ID",
    "DATE_TEXT_ID",
    "DELAYED_CONTROL_ID",
    "DOUBLE_CLICK_BUTTON_ID",
    "DRAG_SURFACE_ID",
    "DUPLICATE_BUTTON_ID",
    "HOTKEY_INDICATOR_ID",
    "KOREAN_TEXT_ID",
    "MODAL_CLOSE_BUTTON_ID",
    "NORMAL_TEXT_ID",
    "OPEN_MODAL_BUTTON_ID",
    "PASSWORD_TEXT_ID",
    "SCROLL_SURFACE_ID",
    "DoubleClickButton",
    "DragSurface",
    "HarnessWindow",
    "OwnedModal",
    "ScrollSurface",
]
