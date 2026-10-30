"""Genera config/datos/claves_filtradas.txt: lista LOCAL de claves comunes o filtradas (Anexo E, T03).

La lista se arma con las claves más frecuentes en filtraciones públicas (en español e inglés) combinadas con los
sufijos y mutaciones típicos. En producción se reemplazaría por una lista grande (por ejemplo, los 100 000 más usados)
cargada en el mismo archivo; el validador solo necesita un archivo con una clave por línea.
"""
from pathlib import Path

BASES = """
password contrasena contraseña clave admin administrador administrator qwerty qwertyuiop asdfghjkl zxcvbnm abc123
letmein welcome bienvenido bienvenida iloveyou teamo teextraño princess princesa dragon monkey football futbol
baseball soccer barcelona realmadrid emelec liga ldu quito guayaquil ecuador iess seguro afiliado portal turnos
master login hello secret superman batman pokemon minecraft whatever trustno1 sunshine shadow michael jennifer
charlie jordan hunter freedom starwars computer internet samsung google facebook whatsapp instagram changeme
cambiame cambiar test prueba usuario user root toor default guest hospital medico doctor salud paciente cita
mexico colombia peru venezuela argentina chile familia amor mivida micasa nicolas daniel alejandro gabriel
sebastian santiago valentina camila sofia isabella mariana fernanda andrea carolina diana paola jessica
""".split()
SUFIJOS = ["", "1", "12", "123", "1234", "12345", "123456", "1234567", "12345678", "123456789", "!", "!!", "@", "#",
           "123!", "1234!", "@123", "#1", "2020", "2021", "2022", "2023", "2024", "2025", "2026", "00", "01", "69", "777"]
EXTRAS = """
123456789012 1234567890123 12345678901234 123456789abc 111111111111 000000000000 222222222222 abcdefghijkl
abcdefghijklmnop qwertyuiop12 qwertyuiop123 qwertyuiopasdf asdfghjkl123 1q2w3e4r5t6y 1qaz2wsx3edc 1q2w3e4r5t6y7u
qazwsxedcrfv zaq12wsxcde3 passwordpassword password1234 password12345 password123456 contrasena123 contraseña123
iloveyou1234 iloveyou12345 letmein12345 welcome12345 administrador1 administrador123 bienvenido123 mipassword123
minombre12345 micontrasena1 portaliess123 iessiess1234 turnosiess123 clave1234567 clave12345678 clave123456789
""".split()


def main() -> None:
    claves = set(EXTRAS)
    for base in BASES:
        for variante in {base, base.capitalize(), base.upper()}:
            for sufijo in SUFIJOS:
                claves.add(variante + sufijo)
    ruta = Path(__file__).resolve().parent.parent / "config" / "datos" / "claves_filtradas.txt"
    ruta.write_text("\n".join(sorted(c.lower() for c in claves)) + "\n", encoding="utf-8")
    print(f"{len(claves)} claves escritas en {ruta}")


if __name__ == "__main__":
    main()
