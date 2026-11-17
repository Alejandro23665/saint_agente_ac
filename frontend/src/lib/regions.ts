/** Países y zonas horarias para los Ajustes del negocio. */

// El servidor guarda el país con su código de 2 letras (ej. VE).
export const COUNTRIES: { code: string; name: string }[] = [
  { code: "VE", name: "Venezuela" },
  { code: "CO", name: "Colombia" },
  { code: "MX", name: "México" },
  { code: "AR", name: "Argentina" },
  { code: "CL", name: "Chile" },
  { code: "PE", name: "Perú" },
  { code: "EC", name: "Ecuador" },
  { code: "BO", name: "Bolivia" },
  { code: "UY", name: "Uruguay" },
  { code: "PY", name: "Paraguay" },
  { code: "PA", name: "Panamá" },
  { code: "CR", name: "Costa Rica" },
  { code: "GT", name: "Guatemala" },
  { code: "HN", name: "Honduras" },
  { code: "SV", name: "El Salvador" },
  { code: "NI", name: "Nicaragua" },
  { code: "DO", name: "República Dominicana" },
  { code: "CU", name: "Cuba" },
  { code: "PR", name: "Puerto Rico" },
  { code: "BR", name: "Brasil" },
  { code: "ES", name: "España" },
  { code: "US", name: "Estados Unidos" },
  { code: "CA", name: "Canadá" },
];

/** Las zonas más usadas por los negocios de la región, para mostrarlas primero. */
export const COMMON_ZONES = [
  "America/Caracas",
  "America/Bogota",
  "America/Mexico_City",
  "America/Lima",
  "America/Santiago",
  "America/Argentina/Buenos_Aires",
  "America/Guayaquil",
  "America/La_Paz",
  "America/Montevideo",
  "America/Asuncion",
  "America/Panama",
  "America/Costa_Rica",
  "America/Santo_Domingo",
  "America/Sao_Paulo",
  "Europe/Madrid",
  "America/New_York",
];

/** Todas las zonas que conoce el navegador (o solo las comunes si no puede listarlas). */
export function allZones(): string[] {
  try {
    const list = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone");
    if (list?.length) return list;
  } catch {
    // Navegador antiguo: se usan las comunes.
  }
  return COMMON_ZONES;
}

/** Países con el actual incluido aunque no esté en la lista corta. */
export function countryOptions(current: string): { code: string; name: string }[] {
  return COUNTRIES.some((c) => c.code === current) || !current ? COUNTRIES : [{ code: current, name: current }, ...COUNTRIES];
}

/** Zonas comunes primero, luego el resto en orden; la actual siempre está. */
export function zoneOptions(current: string): { common: string[]; rest: string[] } {
  const all = allZones();
  const common = COMMON_ZONES.filter((z) => all.includes(z));
  if (current && !common.includes(current) && !all.includes(current)) common.unshift(current);
  const rest = all.filter((z) => !common.includes(z));
  return { common, rest };
}
