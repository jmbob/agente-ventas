#!/usr/bin/env python3
"""Agente de ventas en modo consola. La versión web está en web.py."""

import argparse
import asyncio
import sys

from copilot.session_events import SessionEventType

from agente_core import DEFAULT_MODEL, check_database, create_client, create_session


async def run(question: str | None, model: str) -> None:
    check_database()
    client = create_client()
    await client.start()
    try:
        session = await create_session(client, model)

        def on_event(event):
            if event.type == SessionEventType.ASSISTANT_MESSAGE_DELTA:
                sys.stdout.write(event.data.delta_content or "")
                sys.stdout.flush()
            elif event.type == SessionEventType.SESSION_ERROR:
                print(f"\nError de sesión: {event.data}", file=sys.stderr)

        session.on(on_event)

        async def ask(prompt: str) -> None:
            response = await session.send_and_wait(prompt, timeout=300)
            if response is None:
                raise RuntimeError("El agente terminó sin responder")
            print()

        if question:
            await ask(question)
        else:
            print("📊 Agente de ventas. Escribe 'salir' para terminar.")
            while True:
                try:
                    prompt = input("Tú: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if prompt.lower() in {"salir", "exit", "quit"}:
                    break
                if prompt:
                    print("Asistente: ", end="", flush=True)
                    await ask(prompt)
    finally:
        await client.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agente de análisis de ventas")
    parser.add_argument("--pregunta", help="Pregunta única, sin abrir el modo interactivo")
    parser.add_argument("--modelo", default=DEFAULT_MODEL, help="Modelo de OpenRouter")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.pregunta, args.modelo))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
