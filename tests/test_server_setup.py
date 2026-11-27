import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "setup.sh"


class ServerSetupTests(unittest.TestCase):
    def test_embedded_python_compiles(self):
        source = SETUP.read_text(encoding="utf-8")
        for block in source.split("<<'PY'\n")[1:]:
            compile(block.split("\nPY", 1)[0], "setup.sh", "exec")
        self.assertNotIn(b"\r", SETUP.read_bytes())

    def test_environment_upgrade_preserves_existing_credentials_and_settings(self):
        source = SETUP.read_text(encoding="utf-8")
        code = next(block.split("\nPY", 1)[0] for block in source.split("<<'PY'\n")[1:]
                    if "updates = {" in block)
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "solace.env"
            uploaded = Path(directory) / ".env"
            key = "SYNTHETIC_KEY_FOR_SETUP_TEST"
            env.write_text('SECRET_KEY="example-test-secret"\nSOLACE_SMTP_PASSWORD="unchanged"\n'
                           'SOLACE_REQUIRE_EMAIL=1\nHOST=0.0.0.0\nPORT=8000\n')
            uploaded.write_text(f"YOUTUBE_API_KEY={key}\nSECRET_KEY=ignored\n")
            args = [sys.executable, "-c", code, str(env), "watch.example.test",
                    "support@example.test", "/var/lib/solace/instance", "8080", str(uploaded)]
            subprocess.run(args, check=True, capture_output=True)
            updated = env.read_text()
            self.assertIn('SECRET_KEY="example-test-secret"', updated)
            self.assertIn('SOLACE_SMTP_PASSWORD="unchanged"', updated)
            self.assertIn("SOLACE_REQUIRE_EMAIL=1", updated)
            self.assertIn(f'YOUTUBE_API_KEY="{key}"', updated)
            self.assertIn('SOLACE_INSTANCE_PATH="/var/lib/solace/instance"', updated)
            self.assertIn('TRUST_PROXY="1"', updated)
            self.assertIn('HOST="127.0.0.1"', updated)
            self.assertEqual(updated.count("PORT="), 1)
            subprocess.run(args, check=True, capture_output=True)
            self.assertEqual(dict(line.split("=", 1) for line in updated.splitlines()),
                             dict(line.split("=", 1) for line in env.read_text().splitlines()))

    def test_fresh_setup_without_optional_mail_and_existing_key(self):
        source = SETUP.read_text(encoding="utf-8")
        code = next(block.split("\nPY", 1)[0] for block in source.split("<<'PY'\n")[1:]
                    if "updates = {" in block)
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "solace.env"
            uploaded = Path(directory) / ".env"
            env.write_text('SOLACE_SMTP_HOST=""\nYOUTUBE_API_KEY="existing-synthetic-key"\n')
            uploaded.write_text("YOUTUBE_API_KEY=unused-other-synthetic-key\n")
            args = [sys.executable, "-c", code, str(env), "15.206.210.12",
                    "support@example.test", "/var/lib/solace/instance", "8080", str(uploaded)]
            subprocess.run(args, check=True, capture_output=True)
            self.assertIn('SOLACE_REQUIRE_EMAIL="0"', env.read_text())
            self.assertIn('YOUTUBE_API_KEY="existing-synthetic-key"', env.read_text())

    @unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "Requires Unix Bash")
    def test_nginx_rendering_and_invalid_input(self):
        command = ["bash", str(SETUP)]
        subprocess.run(["bash", "-n", str(SETUP)], check=True)
        for host in ["watch.example.test", "15.206.210.12"]:
            with self.subTest(host=host):
                temporary = subprocess.check_output(command + ["--print-nginx", host, "8080", "128", "http"], text=True)
                self.assertIn("return 503;", temporary)
                self.assertNotIn("listen 443", temporary)
                secure = subprocess.check_output(command + ["--print-nginx", host, "8080", "256", "https"], text=True)
                self.assertIn(f"return 308 https://{host}$request_uri;", secure)
                self.assertIn(f"/etc/letsencrypt/live/{host}/fullchain.pem", secure)
                self.assertIn("proxy_set_header X-Forwarded-Proto $scheme;", secure)
                self.assertIn("proxy_set_header X-Forwarded-For $remote_addr;", secure)
                self.assertIn("client_max_body_size 256m;", secure)
                self.assertIn("proxy_pass http://127.0.0.1:8080;", secure)
                self.assertEqual(secure.count("listen 443 ssl;"), 1)
        invalid = subprocess.run(command + ["--print-nginx", "bad;host", "8080", "128", "https"], capture_output=True)
        self.assertNotEqual(invalid.returncode, 0)

    @unittest.skipUnless(os.name == "posix" and shutil.which("nginx") and shutil.which("openssl"),
                         "Requires Unix Nginx and OpenSSL")
    def test_nginx_accepts_generated_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            certificate = root / "fullchain.pem"
            key = root / "privkey.pem"
            subprocess.run(["openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048", "-days", "1",
                            "-subj", "/CN=watch.example.test", "-keyout", str(key), "-out", str(certificate)],
                           check=True, capture_output=True)
            for mode in ("http", "https"):
                rendered = subprocess.check_output(["bash", str(SETUP), "--print-nginx", "watch.example.test",
                                                   "8080", "128", mode], text=True)
                rendered = rendered.replace("/etc/letsencrypt/live/watch.example.test/", str(root) + "/")
                config = root / "nginx.conf"
                config.write_text(f"pid {root}/nginx.pid;\nerror_log stderr;\nevents {{}}\nhttp {{\n"
                                  f"client_body_temp_path {root}/body;\nproxy_temp_path {root}/proxy;\n"
                                  f"fastcgi_temp_path {root}/fastcgi;\nuwsgi_temp_path {root}/uwsgi;\n"
                                  f"scgi_temp_path {root}/scgi;\naccess_log off;\n{rendered}\n}}\n")
                result = subprocess.run(["nginx", "-t", "-e", "stderr", "-p", str(root), "-c", str(config)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
