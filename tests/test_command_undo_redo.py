from mono.core.command import (
    Command,
    CommandHistory,
    compress_blob,
    decompress_blob,
)


class DummyMutationCommand(Command):
    """Commande de test modifiant une valeur entière."""

    def __init__(self, target_dict: dict[str, int], key: str, new_val: int) -> None:
        self.target: dict[str, int] = target_dict
        self.key: str = key
        self.new_val: int = new_val
        self.old_val: int = target_dict.get(key, 0)

    @property
    def description(self) -> str:
        return f"Mise à jour de {self.key}"

    def execute(self) -> None:
        self.target[self.key] = self.new_val

    def undo(self) -> None:
        self.target[self.key] = self.old_val

    def redo(self) -> None:
        self.target[self.key] = self.new_val


def test_zstd_compression_roundtrip() -> None:
    """Vérifie que la compression zstandard est rapide, réversible et sans perte."""
    original_data = b"BRepTools_Geometry_Snapshot_Data_Chunk_" * 200
    compressed = compress_blob(original_data)
    assert len(compressed) < len(original_data)

    decompressed = decompress_blob(compressed)
    assert decompressed == original_data


def test_command_history_undo_redo() -> None:
    """Vérifie le cycle complet d'exécution, annulation et rétablissement."""
    history = CommandHistory(max_history_size=10)
    data = {"counter": 0}

    assert not history.can_undo
    assert not history.can_redo

    # 1. Exécution de la commande 1 (+10)
    cmd1 = DummyMutationCommand(data, "counter", 10)
    history.execute(cmd1)
    assert data["counter"] == 10
    assert history.can_undo
    assert not history.can_redo

    # 2. Exécution de la commande 2 (+20)
    cmd2 = DummyMutationCommand(data, "counter", 20)
    history.execute(cmd2)
    assert data["counter"] == 20

    # 3. Undo de la commande 2 -> retour à 10
    history.undo()
    assert data["counter"] == 10
    assert history.can_redo

    # 4. Undo de la commande 1 -> retour à 0
    history.undo()
    assert data["counter"] == 0
    assert not history.can_undo

    # 5. Redo -> retour à 10
    history.redo()
    assert data["counter"] == 10

    # 6. Redo -> retour à 20
    history.redo()
    assert data["counter"] == 20
    assert not history.can_redo


def test_set_param_command_undo_redo() -> None:
    """Vérifie la commande réversible SetParamCommand sur un dictionnaire de paramètres."""
    from mono.core.command import SetParamCommand

    class FakeEntity:
        def __init__(self) -> None:
            self.parameters = {"width": 50.0}

    ent = FakeEntity()
    history = CommandHistory()

    cmd = SetParamCommand(ent, "width", 75.0)
    assert "width = 75.0" in cmd.description
    history.execute(cmd)
    assert ent.parameters["width"] == 75.0

    # Undo
    history.undo()
    assert ent.parameters["width"] == 50.0

    # Redo
    history.redo()
    assert ent.parameters["width"] == 75.0

