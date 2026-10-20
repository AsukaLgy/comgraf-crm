import argparse
import getpass
from sqlalchemy import select, text
from .db import SessionLocal, engine
from .models import User, Case, Client, Production
from .security import passwords
from .repository import normalize, balance
from .services import historical_completion_candidates, regularize_historical_completions


def main():
    parser = argparse.ArgumentParser(description='Administración local de Comgraf')
    parser.add_argument('command', choices=['create-admin', 'demo', 'completions-preview', 'regularize-completions'])
    parser.add_argument('--confirm', default='')
    args = parser.parse_args()
    # Keep the same MySQL connection until the advisory lock is released,
    # including across the commit that makes the administrator visible.
    with engine.connect() as connection, SessionLocal(bind=connection) as db:
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
                db.rollback()
                db.execute(text("SELECT RELEASE_LOCK('comgraf_bootstrap')"))
        elif args.command == 'demo':
            if db.scalar(select(Client.id).limit(1)) or db.scalar(select(Case.id).limit(1)):
                raise SystemExit('Demo permitida únicamente en una base sin clientes ni solicitudes. Usa una instalación separada.')
            if input('Escribe DEMOSTRACION para cargar datos ficticios: ') != 'DEMOSTRACION': raise SystemExit('Cancelado')
            for name, subject, kind in [('DEMO · Café del Valle', 'Tarjetas para nueva sucursal', 'Cotización'), ('DEMO · Librería Aurora', 'Consulta sobre libretas A5', 'Consulta'), ('DEMO · Colegio Horizonte', 'Talonarios para matrícula', 'Solicitud')]:
                client = Client(name=name, company=name, search_text=normalize(name), provisional=True)
                db.add(client)
                db.flush()
                db.add(Case(client_id=client.id, subject=subject, kind=kind, channel='Presencial'))
            db.commit()
            print('Datos ficticios cargados explícitamente. No se crearon contraseñas de demostración.')
        else:
            candidates = historical_completion_candidates(db)
            print(f'Solicitudes candidatas: {len(candidates)}')
            for case in candidates:
                client = db.get(Client, case.client_id)
                productions = db.scalars(select(Production).where(Production.case_id == case.id).order_by(Production.id)).all()
                print(f'  CG-{case.id:06d} · {case.subject} · {client.name} · propietario ID {case.owner_id or "sin asignar"}')
                for production in productions:
                    print(f'    OP-{production.id:05d} · {production.currency} {balance(db, case.id, production.id)["balance"]} · {production.stage or "sin clasificar"}')
            if args.command == 'completions-preview':
                print('Vista previa solamente; no se modificó ningún registro.')
            else:
                if args.confirm != 'REGULARIZAR-COMPLETADAS':
                    raise SystemExit('Para ejecutar, vuelve a revisar esta lista y agrega --confirm REGULARIZAR-COMPLETADAS')
                run_id, changed = regularize_historical_completions(db)
                db.commit()
                print(f'Regularizadas: {len(changed)} · actualización {run_id}. Repetir el comando no duplica eventos.')


if __name__ == '__main__': main()
