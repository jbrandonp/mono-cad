"""Explicit engineering units and deterministic conversions.

This initial registry intentionally supports a reviewed subset of SI and common
engineering units. Unknown units fail closed; adapters may add units only with
dimension, scale, and offset tests.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnitDefinition:
    dimension: str
    scale_to_base: float
    offset_to_base: float = 0.0


UNITS: dict[str, UnitDefinition] = {
    # Length — base m
    "m": UnitDefinition("length", 1.0),
    "mm": UnitDefinition("length", 1e-3),
    "cm": UnitDefinition("length", 1e-2),
    "um": UnitDefinition("length", 1e-6),
    "µm": UnitDefinition("length", 1e-6),
    "nm": UnitDefinition("length", 1e-9),
    "angstrom": UnitDefinition("length", 1e-10),
    "Å": UnitDefinition("length", 1e-10),
    "in": UnitDefinition("length", 0.0254),
    "ft": UnitDefinition("length", 0.3048),
    # Area — base m²
    "m^2": UnitDefinition("area", 1.0),
    "mm^2": UnitDefinition("area", 1e-6),
    "cm^2": UnitDefinition("area", 1e-4),
    # Volume — base m³
    "m^3": UnitDefinition("volume", 1.0),
    "L": UnitDefinition("volume", 1e-3),
    "mL": UnitDefinition("volume", 1e-6),
    "cm^3": UnitDefinition("volume", 1e-6),
    # Mass — base kg
    "kg": UnitDefinition("mass", 1.0),
    "g": UnitDefinition("mass", 1e-3),
    "mg": UnitDefinition("mass", 1e-6),
    "tonne": UnitDefinition("mass", 1e3),
    # Time — base s
    "s": UnitDefinition("time", 1.0),
    "ms": UnitDefinition("time", 1e-3),
    "min": UnitDefinition("time", 60.0),
    "h": UnitDefinition("time", 3600.0),
    # Temperature — base K
    "K": UnitDefinition("temperature", 1.0),
    "degC": UnitDefinition("temperature", 1.0, 273.15),
    "degF": UnitDefinition("temperature", 5.0 / 9.0, 255.3722222222222),
    # Velocity — base m/s
    "m/s": UnitDefinition("velocity", 1.0),
    "mm/s": UnitDefinition("velocity", 1e-3),
    "km/h": UnitDefinition("velocity", 1.0 / 3.6),
    # Acceleration — base m/s²
    "m/s^2": UnitDefinition("acceleration", 1.0),
    "g0": UnitDefinition("acceleration", 9.80665),
    # Force — base N
    "N": UnitDefinition("force", 1.0),
    "kN": UnitDefinition("force", 1e3),
    "lbf": UnitDefinition("force", 4.4482216152605),
    # Pressure / stress — base Pa
    "Pa": UnitDefinition("pressure", 1.0),
    "kPa": UnitDefinition("pressure", 1e3),
    "MPa": UnitDefinition("pressure", 1e6),
    "GPa": UnitDefinition("pressure", 1e9),
    "bar": UnitDefinition("pressure", 1e5),
    # Energy — base J
    "J": UnitDefinition("energy", 1.0),
    "kJ": UnitDefinition("energy", 1e3),
    "Wh": UnitDefinition("energy", 3600.0),
    "kWh": UnitDefinition("energy", 3.6e6),
    # Power — base W
    "W": UnitDefinition("power", 1.0),
    "kW": UnitDefinition("power", 1e3),
    # Frequency — base Hz
    "Hz": UnitDefinition("frequency", 1.0),
    "kHz": UnitDefinition("frequency", 1e3),
    "MHz": UnitDefinition("frequency", 1e6),
    # Density — base kg/m³
    "kg/m^3": UnitDefinition("density", 1.0),
    "g/cm^3": UnitDefinition("density", 1e3),
    # Angle — base rad
    "rad": UnitDefinition("angle", 1.0),
    "deg": UnitDefinition("angle", 3.141592653589793 / 180.0),
    # Torque — base N·m
    "N*m": UnitDefinition("torque", 1.0),
    "kN*m": UnitDefinition("torque", 1e3),
    # Dynamic viscosity — base Pa·s
    "Pa*s": UnitDefinition("dynamic_viscosity", 1.0),
    "mPa*s": UnitDefinition("dynamic_viscosity", 1e-3),
    # Electrical / electromagnetic
    "A": UnitDefinition("electric_current", 1.0),
    "mA": UnitDefinition("electric_current", 1e-3),
    "V": UnitDefinition("voltage", 1.0),
    "kV": UnitDefinition("voltage", 1e3),
    "C": UnitDefinition("electric_charge", 1.0),
    "Wb": UnitDefinition("magnetic_flux", 1.0),
    "T": UnitDefinition("magnetic_flux_density", 1.0),
    "mT": UnitDefinition("magnetic_flux_density", 1e-3),
    # Dimensionless
    "1": UnitDefinition("dimensionless", 1.0),
    "%": UnitDefinition("dimensionless", 0.01),
}


def get_unit_definition(unit: str) -> UnitDefinition:
    try:
        return UNITS[unit]
    except KeyError as exc:
        raise ValueError(f"Unité non prise en charge : {unit!r}") from exc


def convert_value(value: float, from_unit: str, to_unit: str) -> float:
    source = get_unit_definition(from_unit)
    target = get_unit_definition(to_unit)
    if source.dimension != target.dimension:
        raise ValueError(
            f"Conversion dimensionnellement impossible : {from_unit} ({source.dimension}) "
            f"vers {to_unit} ({target.dimension})"
        )
    base_value = value * source.scale_to_base + source.offset_to_base
    return (base_value - target.offset_to_base) / target.scale_to_base


def validate_unit_dimension(unit: str, dimension: str) -> None:
    definition = get_unit_definition(unit)
    canonical_dimension = {"stress": "pressure"}.get(dimension, dimension)
    if definition.dimension != canonical_dimension:
        raise ValueError(
            f"L'unité {unit!r} a la dimension {definition.dimension!r}, "
            f"pas {dimension!r}"
        )
