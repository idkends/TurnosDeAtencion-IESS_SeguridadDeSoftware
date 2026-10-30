"""Respaldos cifrados con PITR y prueba de restauración con RPO/RTO medidos (Anexo I, Etapa 3)."""
import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
EVIDENCIAS = sorted((RAIZ / "docs" / "evidencia").glob("restauracion_*.json"))


def test_T08_el_compose_archiva_wal_y_guarda_copias_fuera_del_volumen_de_la_base():
    texto = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
    assert "archive_mode=on" in texto and "wal_level=replica" in texto and "archive_command=" in texto
    bloque_db = texto.split("\n  db:")[1].split("\n  redis:")[0]
    assert "pgdata:/var/lib/postgresql" in bloque_db
    assert "wal_archivo:/wal_archivo" in bloque_db and "respaldos:/respaldos" in bloque_db  # volúmenes distintos de pgdata
    assert "ports:" not in bloque_db  # la base sigue sin exponerse al host


def test_los_scripts_cifran_miden_y_no_llevan_secretos():
    respaldo = (RAIZ / "scripts" / "respaldo.sh").read_text(encoding="utf-8")
    restaurar = (RAIZ / "scripts" / "restaurar.sh").read_text(encoding="utf-8")
    assert "openssl enc -aes-256-cbc -pbkdf2" in respaldo and "pass env:RESPALDO_CLAVE" in respaldo
    assert "RESPALDO_REALIZADO" in respaldo and "RESPALDO_FALLIDO" in respaldo
    assert "recovery.signal" in restaurar and "restore_command" in restaurar
    assert "rpo_segundos" in restaurar and "rto_segundos" in restaurar and "docs/evidencia" in restaurar
    for texto in (respaldo, restaurar):
        assert "PGPASSWORD=\"" not in texto.replace('PGPASSWORD="$POSTGRES_PASSWORD"', "")  # solo vienen del entorno


def test_la_clave_de_respaldo_es_una_variable_de_entorno_sin_valor_en_el_repositorio():
    ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
    assert "RESPALDO_CLAVE=\n" in ejemplo
    assert "respaldos_locales/" in (RAIZ / ".gitignore").read_text(encoding="utf-8")


@pytest.mark.skipif(not EVIDENCIAS, reason="aún no se ejecutó scripts/restaurar.sh")
def test_T08_la_ultima_prueba_de_restauracion_cumple_rpo_y_rto():
    ev = json.loads(EVIDENCIAS[-1].read_text(encoding="utf-8"))
    assert ev["cadena_auditoria"] == "íntegra"
    assert ev["rpo_segundos"] <= ev["rpo_objetivo_segundos"] == 300
    assert ev["rto_segundos"] <= ev["rto_objetivo_segundos"] == 1800
    assert ev["cumple_objetivos"] is True and ev["usuarios_restaurados"] > 0
