"""Private JSON-lines transport to the owning Perl LiveAPI process."""
import json
import sys

from cct_core import CleanupError

MAX_FRAME = 16 * 1024 * 1024


class PipeTransport:
    def __init__(self, reader=None, writer=None):
        self.reader = reader if reader is not None else sys.stdin
        self.writer = writer if writer is not None else sys.stdout

    def read_frame(self):
        line = self.reader.readline(MAX_FRAME + 1)
        try:
            if not line or len(line) > MAX_FRAME or not line.endswith("\n"):
                raise ValueError("Incomplete frame")
            frame = json.loads(line)
            if not isinstance(frame, dict):
                raise ValueError("Invalid frame")
            return frame
        except (ValueError, TypeError) as exc:
            raise CleanupError("Se interrumpió la conexión con LiveAPI de cPanel.") from exc

    def write_frame(self, frame):
        line = json.dumps(frame, ensure_ascii=True, separators=(",", ":"))
        if len(line) > MAX_FRAME:
            raise CleanupError("La respuesta de cPanel excede el límite del plugin.")
        self.writer.write(line + "\n")
        self.writer.flush()

    def call(self, version, module, function, params):
        self.write_frame({"type": "api", "version": version, "module": module,
                          "function": function, "params": params})
        result = self.read_frame()
        if result.get("type") != "api_result" or not result.get("ok"):
            raise CleanupError("No se pudo consultar LiveAPI de cPanel (" + module + "/" + function + ").")
        return result.get("document")
