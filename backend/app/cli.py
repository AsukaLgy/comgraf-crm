import argparse
import getpass
from sqlalchemy import select, text
from .db import SessionLocal
from .models import User, Case, Client
from .security import passwords
from .repository import normalize


def main():
    parser = argparse.ArgumentParser(description='Administración local de Comgraf')
    parser.add_argument('command', choices=['create-admin', 'demo'])
    args = parser.parse_args()
    with SessionLocal.begin() as db:
        if args.command == 'create-admin':
            # MySQL advisory lock prevents concurrent first-admin bootstrap.
            if db.scalar(text("SELECT GET_LOCK('comgraf_bootstrap', 10)")) != 1:
                raise SystemExit('Otro proceso está creando el administrador')
            try:
                if db.scalar(select(User.id).limit(1)):
                    raise SystemExit('Ya existen usuarios. Administra las cuentas desde Configuración.')
                username = input('Usuario administrador: ').strip().lower()
                name = input('Nombre: ').strip()
                password = getpass.getpass('Contraseña nueva (mínimo 12 caracteres): ')
                confirmation = getpass.getpass('Repite la contraseña: ')
                from .schemas import UserInput
                data = UserInput(username=username, name=name, password=password, roles=['Administrador'])
                if password != confirmation: raise SystemExit('Las contraseñas no coinciden')
                db.add(User(username=data.username, name=data.name, password_hash=passwords.hash(password), roles=data.roles))
                db.commit()
                print('Administrador creado. No se guardó la contraseña en texto.')
            finally:
                db.execute(text("SELECT RELEASE_LOCK('comgraf_bootstrap')"))
        else:
            if db.scalar(select(Client.id).limit(1)) or db.scalar(select(Case.id).limit(1)):
                raise SystemExit('Demo permitida únicamente en una base sin clientes ni casos. Usa una instalación separada.')
            if input('Escribe DEMOSTRACION para cargar datos ficticios: ') != 'DEMOSTRACION': raise SystemExit('Cancelado')
            for name, subject, kind in [('DEMO · Café del Valle', 'Tarjetas para nueva sucursal', 'Cotización'), ('DEMO · Librería Aurora', 'Consulta sobre libretas A5', 'Consulta'), ('DEMO · Colegio Horizonte', 'Talonarios para matrícula', 'Solicitud')]:
                client = Client(name=name, company=name, search_text=normalize(name), provisional=True)
                db.add(client)
                db.flush()
                db.add(Case(client_id=client.id, subject=subject, kind=kind, channel='Presencial'))
            print('Datos ficticios cargados explícitamente. No se crearon contraseñas de demostración.')


if __name__ == '__main__': main()
