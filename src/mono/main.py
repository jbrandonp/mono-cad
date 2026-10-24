from __future__ import annotations

import uvicorn


def main() -> None:
    """Point d'entrée principal pour démarrer le serveur d'ingénierie Mono en local."""
    print("===================================================================")
    print(" Démarrage de Mono Backend v2.0 (Environnement d'Ingénierie IA)")
    print(" Mode natif Windows, zero Docker, IPC WebSocket & API REST active")
    print("===================================================================")
    uvicorn.run("mono.ipc.server:app", host="127.0.0.1", port=8000, reload=False, log_level="info")


if __name__ == "__main__":
    main()
