from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from uuid import UUID

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex, Qt

from universal_rpa.domain.errors import ValidationReport
from universal_rpa.domain.workflow import IfPresentStep, LoopStep, Step, Workflow

_INVALID_INDEX = QModelIndex()
type ModelIndex = QModelIndex | QPersistentModelIndex


class StepTreeRole(IntEnum):
    STEP_ID = int(Qt.ItemDataRole.UserRole) + 1
    KIND = int(Qt.ItemDataRole.UserRole) + 2
    ENABLED = int(Qt.ItemDataRole.UserRole) + 3
    VALIDATION_SEVERITY = int(Qt.ItemDataRole.UserRole) + 4


@dataclass(slots=True)
class _Node:
    step: Step | None
    parent: _Node | None = None
    children: list[_Node] = field(default_factory=list)

    def row(self) -> int:
        if self.parent is None:
            return 0
        return self.parent.children.index(self)


class WorkflowTreeModel(QAbstractItemModel):
    def __init__(self) -> None:
        super().__init__()
        self._root = _Node(None)
        self._nodes: dict[UUID, _Node] = {}
        self._severity: dict[UUID, str] = {}

    def set_workflow(self, workflow: Workflow | None) -> None:
        self.beginResetModel()
        self._root = _Node(None)
        self._nodes.clear()
        self._severity.clear()
        if workflow is not None:
            self._build(workflow.steps, self._root)
        self.endResetModel()

    def set_validation(self, report: ValidationReport) -> None:
        severity: dict[UUID, str] = {}
        for issue in report.issues:
            if issue.step_id is None:
                continue
            current = severity.get(issue.step_id)
            if issue.severity == "error" or current is None:
                severity[issue.step_id] = issue.severity
        self._severity = severity
        if self.rowCount() > 0:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.rowCount() - 1, 0),
                [int(StepTreeRole.VALIDATION_SEVERITY)],
            )

    def index(
        self,
        row: int,
        column: int,
        parent: ModelIndex = _INVALID_INDEX,
    ) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        parent_node = self._node(parent)
        return self.createIndex(row, column, parent_node.children[row])

    def parent(self, index: ModelIndex) -> QModelIndex:  # type: ignore[override]
        if not index.isValid():
            return QModelIndex()
        node = self._node(index)
        parent = node.parent
        if parent is None or parent is self._root:
            return QModelIndex()
        return self.createIndex(parent.row(), 0, parent)

    def rowCount(self, parent: ModelIndex = _INVALID_INDEX) -> int:
        if parent.column() > 0:
            return 0
        return len(self._node(parent).children)

    def columnCount(self, parent: ModelIndex = _INVALID_INDEX) -> int:
        del parent
        return 1

    def data(self, index: ModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> object:
        if not index.isValid():
            return None
        step = self._node(index).step
        if step is None:
            return None
        if role == int(Qt.ItemDataRole.DisplayRole):
            prefix = {"action": "동작", "loop": "반복", "if_present": "조건"}[step.kind]
            disabled = " · 꺼짐" if not step.enabled else ""
            return f"{prefix} · {step.label}{disabled}"
        if role == int(Qt.ItemDataRole.CheckStateRole):
            return Qt.CheckState.Checked if step.enabled else Qt.CheckState.Unchecked
        if role == int(StepTreeRole.STEP_ID):
            return step.step_id
        if role == int(StepTreeRole.KIND):
            return step.kind
        if role == int(StepTreeRole.ENABLED):
            return step.enabled
        if role == int(StepTreeRole.VALIDATION_SEVERITY):
            return self._severity.get(step.step_id)
        return None

    def flags(self, index: ModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsDropEnabled
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsDropEnabled
        )

    def step_id(self, index: QModelIndex) -> UUID | None:
        if not index.isValid():
            return None
        step = self._node(index).step
        return step.step_id if step is not None else None

    def step(self, index: QModelIndex) -> Step | None:
        if not index.isValid():
            return None
        return self._node(index).step

    def index_for_step(self, step_id: UUID) -> QModelIndex:
        node = self._nodes.get(step_id)
        if node is None:
            return QModelIndex()
        return self.createIndex(node.row(), 0, node)

    def can_move(self, step_id: UUID, *, under: UUID | None) -> bool:
        moving = self._nodes.get(step_id)
        if moving is None:
            return False
        parent = self._root if under is None else self._nodes.get(under)
        if parent is None:
            return False
        if parent is not self._root and not isinstance(parent.step, (LoopStep, IfPresentStep)):
            return False
        cursor: _Node | None = parent
        while cursor is not None:
            if cursor is moving:
                return False
            cursor = cursor.parent
        ancestor_loop_depth = 0
        cursor = parent
        while cursor is not None:
            if isinstance(cursor.step, LoopStep):
                ancestor_loop_depth += 1
            cursor = cursor.parent
        return ancestor_loop_depth + self._subtree_loop_depth(moving) <= 2

    def _build(self, steps: tuple[Step, ...], parent: _Node) -> None:
        for step in steps:
            node = _Node(step=step, parent=parent)
            parent.children.append(node)
            self._nodes[step.step_id] = node
            if isinstance(step, (LoopStep, IfPresentStep)):
                self._build(step.steps, node)

    def _node(self, index: ModelIndex) -> _Node:
        if not index.isValid():
            return self._root
        pointer = index.internalPointer()
        return pointer if isinstance(pointer, _Node) else self._root

    @classmethod
    def _subtree_loop_depth(cls, node: _Node) -> int:
        own = 1 if isinstance(node.step, LoopStep) else 0
        child_depth = max((cls._subtree_loop_depth(child) for child in node.children), default=0)
        return own + child_depth


__all__ = ["StepTreeRole", "WorkflowTreeModel"]
