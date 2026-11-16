"""Crear un usuario del panel y darle acceso a un negocio.

Uso (la contraseña se pide en la terminal y no se ve al escribirla):
    uv run python -m app.scripts.crear_usuario andre@saint.ai "Andre" --negocio panaderia-prueba
    uv run python -m app.scripts.crear_usuario ana@x.com Ana --negocio panaderia-prueba --rol agent
    uv run python -m app.scripts.crear_usuario andre@saint.ai "Andre" --cambiar-clave

Si el correo ya existe, no se crea otro usuario: solo se le da acceso al negocio (o se le
cambia la contraseña con --cambiar-clave, lo que además cierra todas sus sesiones).
Roles: owner (dueño), admin (administrador) y agent (atiende conversaciones).
"""

import argparse
import asyncio
import getpass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.auth_tokens import MIN_PASSWORD_LENGTH, hash_password
from app.db.session import SessionFactory, tenant_session
from app.models import TenantMembership, User
from app.models.enums import MembershipRole
from app.scripts.enviar_mensaje import find_tenant
from app.services.auth import revoke_all


def ask_password() -> str:
    while True:
        password = getpass.getpass("Contraseña nueva: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")
            continue
        if getpass.getpass("Repítela: ") != password:
            print("No coinciden. Otra vez.")
            continue
        return password


async def main_async(
    email: str, name: str, slug: str | None, role: MembershipRole, change_password: bool
) -> None:
    email = email.strip()
    async with SessionFactory() as session, session.begin():
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email, full_name=name.strip(), password_hash=hash_password(ask_password())
            )
            session.add(user)
            await session.flush()
            print(f"Usuario creado: {email}")
        elif change_password:
            user.password_hash = hash_password(ask_password())
            print(f"Contraseña cambiada: {email}")
        else:
            print(f"El usuario {email} ya existía; no se cambió su contraseña.")
        user_id = user.id
    if change_password:
        await revoke_all(user_id)
        print("Se cerraron sus sesiones abiertas.")

    if slug:
        tenant_id = await find_tenant(slug)
        async with tenant_session(tenant_id) as session:
            result = await session.execute(
                insert(TenantMembership)
                .values(tenant_id=tenant_id, user_id=user_id, role=role)
                .on_conflict_do_update(index_elements=["tenant_id", "user_id"], set_={"role": role})
            )
        print(f"Acceso a {slug} como {role.value}." if result.rowcount else "Sin cambios.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("correo")
    parser.add_argument("nombre", help="nombre que se verá en el panel")
    parser.add_argument("--negocio", help="slug del negocio, ej. panaderia-prueba")
    parser.add_argument(
        "--rol",
        default="owner",
        choices=[r.value for r in MembershipRole],
        help="por defecto owner",
    )
    parser.add_argument("--cambiar-clave", action="store_true", help="pide una contraseña nueva")
    args = parser.parse_args()
    asyncio.run(
        main_async(
            args.correo, args.nombre, args.negocio, MembershipRole(args.rol), args.cambiar_clave
        )
    )


if __name__ == "__main__":
    main()
