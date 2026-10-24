from __future__ import annotations

import json
from typing import Set
from fastapi import WebSocket

from mono.core.geometry_provider import BinaryMeshPayload


class WebSocketIPCManager:
    """Gestionnaire de connexions WebSocket hybrides (Contrôle JSON + Trames Binaires Zero-Copy).

    Maintient le pool de clients connectés (ex: frontend Tauri/Three.js) et assure
    la diffusion ordonnée des en-têtes JSON suivis immédiatement de leurs octets bruts.
    """

    def __init__(self) -> None:
        """Initialise le gestionnaire avec un ensemble vide de connexions actives."""
        self._active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        """Accepte et enregistre une nouvelle connexion client.

        Args:
            websocket: Instance de connexion FastAPI WebSocket.
        """
        await websocket.accept()
        self._active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """Retire une connexion fermée du pool actif.

        Args:
            websocket: Connexion à détacher.
        """
        self._active_connections.discard(websocket)

    async def send_json(self, websocket: WebSocket, data: dict[str, object]) -> None:
        """Transmet un message de contrôle sérialisé en JSON vers un client spécifique.

        Args:
            websocket: Client destinataire.
            data: Dictionnaire Python à sérialiser.
        """
        await websocket.send_text(json.dumps(data))

    async def broadcast_json(self, data: dict[str, object]) -> None:
        """Diffuse un message JSON à tous les clients connectés.

        Args:
            data: Message à diffuser.
        """
        payload_text = json.dumps(data)
        for connection in list(self._active_connections):
            try:
                await connection.send_text(payload_text)
            except Exception:
                self.disconnect(connection)

    async def send_binary_mesh(self, websocket: WebSocket, mesh: BinaryMeshPayload) -> None:
        """Transmet un maillage en deux étapes : en-tête descriptif JSON puis données binaires pures.

        Args:
            websocket: Client destinataire.
            mesh: Charge utile binaire contenant les sommets, normales et triangles.
        """
        vertex_count = int(mesh.vertices.shape[0])
        triangle_count = int(mesh.indices.shape[0])

        header: dict[str, object] = {
            "type": "binary_mesh_header",
            "entity_id": mesh.entity_id,
            "vertex_count": vertex_count,
            "triangle_count": triangle_count,
            "byte_size": len(mesh.to_bytes()),
        }

        # 1. En-tête JSON
        await websocket.send_text(json.dumps(header))
        # 2. Flux binaire aligné zero-copy
        await websocket.send_bytes(mesh.to_bytes())

    async def broadcast_binary_mesh(self, mesh: BinaryMeshPayload) -> None:
        """Diffuse un maillage géométrique binaire à l'ensemble des clients connectés.

        Args:
            mesh: Charge utile binaire à diffuser.
        """
        for connection in list(self._active_connections):
            try:
                await self.send_binary_mesh(connection, mesh)
            except Exception:
                self.disconnect(connection)
