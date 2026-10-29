"""Jalankan dari folder mana pun: python scripts/generate_proto.py."""

from pathlib import Path
import sys

from grpc_tools import protoc


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "generated"
    output.mkdir(exist_ok=True)
    result = protoc.main([
        "grpc_tools.protoc",
        f"-I{root / 'proto'}",
        f"--python_out={output}",
        f"--grpc_python_out={output}",
        str(root / "proto" / "cinema.proto"),
    ])
    if result:
        return result
    # protoc menghasilkan import absolut; ubah agar generated adalah package.
    grpc_file = output / "cinema_pb2_grpc.py"
    source = grpc_file.read_text(encoding="utf-8")
    source = source.replace(
        "import cinema_pb2 as cinema__pb2",
        "from . import cinema_pb2 as cinema__pb2",
    )
    grpc_file.write_text(source, encoding="utf-8")
    (output / "__init__.py").touch()
    print("Berhasil generate generated/cinema_pb2.py dan cinema_pb2_grpc.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
