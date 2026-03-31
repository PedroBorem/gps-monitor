from __future__ import annotations

import argparse
import json
import logging
import os
import ssl
import threading
import time
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
VIEWER_DIR = ROOT / "viewer"
MESSAGES_FILE = DATA_DIR / "messages.jsonl"
LATEST_FILE = DATA_DIR / "latest.json"
STATUS_FILE = DATA_DIR / "status.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config(config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["endpoint"] = os.getenv("AWS_IOT_ENDPOINT", config.get("endpoint", "")).strip()
    config["client_id"] = os.getenv("AWS_IOT_CLIENT_ID", config.get("client_id", "gps-monitor"))
    return config


class JsonFileStore:
    def __init__(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        self._lock = threading.Lock()
        if not MESSAGES_FILE.exists():
            MESSAGES_FILE.write_text("", encoding="utf-8")
        if not LATEST_FILE.exists():
            LATEST_FILE.write_text("{}", encoding="utf-8")
        if not STATUS_FILE.exists():
            self.write_status(
                {
                    "timestamp": utc_now(),
                    "connected": False,
                    "endpoint": "",
                    "client_id": "",
                    "publish_topic": "",
                    "subscribe_topic": "",
                }
            )

    def write_status(self, status: dict[str, Any]) -> None:
        with self._lock:
            STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    def append_message(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False)
        with self._lock:
            with MESSAGES_FILE.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            LATEST_FILE.write_text(json.dumps(message, ensure_ascii=False, indent=2), encoding="utf-8")


class AwsIotMonitor:
    def __init__(self, config: dict[str, Any], store: JsonFileStore) -> None:
        self.config = config
        self.store = store
        self.stop_event = threading.Event()
        self.connected_event = threading.Event()
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=config["client_id"],
            clean_session=True,
        )
        self.client.enable_logger(logging.getLogger("mqtt"))
        self.client.tls_set(
            ca_certs=str((ROOT / config["ca_cert"]).resolve()),
            certfile=str((ROOT / config["device_cert"]).resolve()),
            keyfile=str((ROOT / config["private_key"]).resolve()),
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.on_publish = self.on_publish

    def on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        logging.info("Conectado ao broker com código %s", reason_code)
        self.connected_event.set()
        self.store.write_status(
            {
                "timestamp": utc_now(),
                "connected": True,
                "endpoint": self.config["endpoint"],
                "client_id": self.config["client_id"],
                "publish_topic": self.config["publish_topic"],
                "subscribe_topic": self.config["subscribe_topic"],
            }
        )
        client.subscribe(self.config["subscribe_topic"], qos=1)

    def on_disconnect(self, client: mqtt.Client, userdata: Any, disconnect_flags: Any, reason_code: Any, properties: Any) -> None:
        logging.warning("Desconectado do broker com código %s", reason_code)
        self.connected_event.clear()
        self.store.write_status(
            {
                "timestamp": utc_now(),
                "connected": False,
                "endpoint": self.config["endpoint"],
                "client_id": self.config["client_id"],
                "publish_topic": self.config["publish_topic"],
                "subscribe_topic": self.config["subscribe_topic"],
                "reason_code": str(reason_code),
            }
        )

    def on_publish(self, client: mqtt.Client, userdata: Any, mid: int, reason_code: Any, properties: Any) -> None:
        logging.info("Publicado em %s (%s)", self.config["publish_topic"], mid)

    def on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        payload_bytes = bytes(msg.payload)
        try:
            payload_text = payload_bytes.decode("utf-8")
        except UnicodeDecodeError:
            payload_text = payload_bytes.hex()

        record = {
            "timestamp": utc_now(),
            "topic": msg.topic,
            "qos": msg.qos,
            "retain": msg.retain,
            "payload": payload_text,
        }
        self.store.append_message(record)
        logging.info("Mensagem recebida em %s: %s", msg.topic, payload_text)

    def connect(self) -> None:
        if not self.config["endpoint"]:
            raise ValueError("Defina o endpoint da AWS IoT Core em config.json ou AWS_IOT_ENDPOINT.")

        self.client.connect(self.config["endpoint"], port=self.config.get("port", 8883), keepalive=60)
        self.client.loop_start()

    def disconnect(self) -> None:
        self.stop_event.set()
        self.client.loop_stop()
        self.client.disconnect()

    def publisher_loop(self) -> None:
        interval = int(self.config.get("publish_interval_seconds", 30))
        payload = self.config.get("publish_payload", "#07$")

        while not self.stop_event.is_set():
            if self.connected_event.wait(timeout=1):
                result = self.client.publish(self.config["publish_topic"], payload=payload, qos=1)
                result.wait_for_publish()
            if self.stop_event.wait(interval):
                break


def start_http_server(host: str, port: int) -> ThreadingHTTPServer:
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT))
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, name="viewer-server", daemon=True)
    thread.start()
    logging.info("Visualização disponível em http://%s:%s/viewer/", host, port)
    return server


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor simples para AWS IoT Core.")
    parser.add_argument("--config", default="config.json", help="Arquivo JSON de configuração.")
    parser.add_argument("--serve", action="store_true", help="Inicia um servidor HTTP local para a visualização.")
    parser.add_argument("--host", default="127.0.0.1", help="Host do servidor HTTP local.")
    parser.add_argument("--port", default=8080, type=int, help="Porta do servidor HTTP local.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()

    config = load_config(ROOT / args.config)
    store = JsonFileStore()
    monitor = AwsIotMonitor(config, store)

    http_server: ThreadingHTTPServer | None = None
    publisher_thread: threading.Thread | None = None

    try:
        if args.serve:
            http_server = start_http_server(args.host, args.port)

        monitor.connect()
        publisher_thread = threading.Thread(target=monitor.publisher_loop, name="publisher", daemon=True)
        publisher_thread.start()

        logging.info("Monitor em execução. Pressione Ctrl+C para encerrar.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Encerrando monitor...")
    finally:
        monitor.disconnect()
        if publisher_thread and publisher_thread.is_alive():
            publisher_thread.join(timeout=5)
        if http_server:
            http_server.shutdown()
            http_server.server_close()


if __name__ == "__main__":
    main()
