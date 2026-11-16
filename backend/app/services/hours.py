"""Horario de atención del negocio (bloque 5.5).

Se guarda en tenants.business_hours así:

    {
      "days": {
        "mon": [["08:00", "12:00"], ["14:00", "18:00"]],
        "tue": [["08:00", "18:00"]],
        "sun": []
      },
      "after_hours_message": "Ya cerramos. Mañana a las 8 te respondemos."
    }

- Las horas son del huso horario del negocio (tenants.timezone).
- Un día que falta o con [] está cerrado. Hasta 3 tramos por día, sin cruzar la
  medianoche ("24:00" vale como fin del día).
- Sin "days" (negocio que no configuró horario) se considera abierto siempre.

Aquí solo se guarda y se valida. Qué hace el bot fuera de horario (avisar y pasar a una
persona) se programa en el bloque 4.6, que usará is_open().
"""

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from app.services.members import InvalidInputError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = {
    "mon": "lunes",
    "tue": "martes",
    "wed": "miércoles",
    "thu": "jueves",
    "fri": "viernes",
    "sat": "sábado",
    "sun": "domingo",
}
MAX_RANGES_PER_DAY = 3
MAX_MESSAGE = 500


def _parse(value: str, day: str) -> time:
    try:
        hours, minutes = value.split(":")
        h, m = int(hours), int(minutes)
    except (ValueError, AttributeError) as exc:
        raise InvalidInputError(
            f"Hora no válida el {DAY_NAMES[day]}: {value!r} (usa HH:MM)"
        ) from exc
    if (h, m) == (24, 0):
        return time.max
    if not (0 <= h <= 23 and 0 <= m <= 59 and len(minutes) == 2):
        raise InvalidInputError(f"Hora no válida el {DAY_NAMES[day]}: {value!r} (usa HH:MM)")
    return time(h, m)


def validate(days: dict[str, list[list[str]]], after_hours_message: str | None) -> dict[str, Any]:
    """Revisa el horario y lo devuelve limpio (tramos ordenados), listo para guardar."""
    unknown = set(days) - set(DAYS)
    if unknown:
        raise InvalidInputError(f"Días no válidos: {', '.join(sorted(unknown))}")
    clean: dict[str, list[list[str]]] = {}
    for day in DAYS:
        ranges = days.get(day, [])
        if len(ranges) > MAX_RANGES_PER_DAY:
            raise InvalidInputError(f"El {DAY_NAMES[day]} admite hasta {MAX_RANGES_PER_DAY} tramos")
        parsed = []
        for pair in ranges:
            if len(pair) != 2:
                raise InvalidInputError(f"Cada tramo del {DAY_NAMES[day]} va como [inicio, fin]")
            start, end = _parse(pair[0], day), _parse(pair[1], day)
            if start >= end:
                raise InvalidInputError(
                    f"El {DAY_NAMES[day]}: la hora de cierre debe ser después de la de apertura"
                )
            parsed.append((start, end, pair))
        parsed.sort()
        for (_, prev_end, _), (start, _, _) in zip(parsed, parsed[1:], strict=False):
            if start < prev_end:
                raise InvalidInputError(f"El {DAY_NAMES[day]} tiene tramos que se pisan")
        clean[day] = [[p[0], p[1]] for _, _, p in parsed]
    message = " ".join((after_hours_message or "").split()) or None
    if message and len(message) > MAX_MESSAGE:
        raise InvalidInputError(
            f"El mensaje fuera de horario admite hasta {MAX_MESSAGE} caracteres"
        )
    return {"days": clean, "after_hours_message": message}


def is_open(business_hours: dict[str, Any] | None, timezone: str, at: datetime) -> bool:
    """¿Está abierto el negocio en ese momento? Sin horario configurado: siempre sí."""
    days = (business_hours or {}).get("days")
    if not days:
        return True
    local = at.astimezone(ZoneInfo(timezone))
    day = DAYS[local.weekday()]
    now = local.time().replace(tzinfo=None)
    return any(_parse(start, day) <= now < _parse(end, day) for start, end in days.get(day, []))
