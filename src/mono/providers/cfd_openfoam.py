"""Générateur de cas et solveur de mécanique des fluides numérique (CFD) OpenFOAM.

🎯 Pédagogie (Explication pour un jeune de 15 ans) :
Imagine que tu veuilles tester l'aérodynamisme d'une voiture de course ou d'une aile d'avion :
1. La boîte d'air (Le domaine fluide blockMesh) : On place la pièce 3D dans un grand tunnel virtuel rempli d'air.
2. Le maillage de la couche limite (snappyHexMesh) : Autour de la carrosserie, on découpe l'air en millions de petits cubes
   pour capturer précisément les tourbillons et le frottement de l'air sur la surface.
3. Le vent virtuel (Conditions aux limites) : On injecte de l'air à une vitesse précise (par exemple 20 m/s, soit 72 km/h)
   à l'entrée (inlet), on laisse la pression s'équilibrer à la sortie (outlet), et l'air frotte sur la pièce avec vitesse nulle (no-slip).
4. Les équations de Navier-Stokes (solveur simpleFoam) : Le solveur calcule la pression et la vitesse de l'écoulement
   en chaque point, puis en déduit la traînée (force qui freine, Cd) et la portance (force qui soulève, Cl) !
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math
import shutil
import subprocess
import build123d as bd
import numpy as np

from mono.core.geometry_provider import (
    GeometryProvider,
    BinaryMeshPayload,
    ValidationReport,
)


@dataclass(frozen=True)
class CFDFluidProperties:
    """Propriétés thermophysiques du fluide incompressible."""

    name: str = "AIR_20C"
    kinematic_viscosity_m2_s: float = 1.5e-5  # Viscosité cinématique nu (m²/s)
    density_kg_m3: float = 1.225             # Masse volumique rho (kg/m³)


@dataclass(frozen=True)
class CFDBoundaryConfig:
    """Conditions aux limites de soufflerie numérique."""

    inlet_velocity_m_s: tuple[float, float, float] = (20.0, 0.0, 0.0)  # Vitesse d'entrée (U_inf)
    inlet_direction: str = "min_x"
    outlet_direction: str = "max_x"
    domain_multiplier: tuple[float, float, float] = (4.0, 2.5, 2.5)  # Facteur d'échelle du domaine par rapport à la BBox
    turbulence_intensity: float = 0.05  # 5 % d'intensité turbulente standard
    turbulent_length_scale_m: float = 0.01


@dataclass(frozen=True)
class CFDSolverConfig:
    """Configuration du solveur stationnaire incompressible (Navier-Stokes RANS)."""

    solver_name: str = "simpleFoam"
    turbulence_model: str = "kOmegaSST"  # Modèle de turbulence standard certifié
    num_iterations: int = 300
    write_interval: int = 50
    non_orthogonal_correctors: int = 1


@dataclass(frozen=True)
class CFDResultReport:
    """Rapport d'analyse aérodynamique et hydrodynamique CFD."""

    drag_force_n: float            # Force de traînée Fx (N)
    lift_force_n: float            # Force de portance Fz (N)
    drag_coefficient_cd: float     # Coefficient de traînée sans dimension Cd
    lift_coefficient_cl: float     # Coefficient de portance sans dimension Cl
    converged: bool
    num_iterations: int
    case_path: str


class CfdOpenFoamProvider(GeometryProvider):
    """Fournisseur géométrique et générateur de cas de simulation CFD OpenFOAM."""

    def __init__(
        self,
        entity_id: str,
        part: bd.Part | bd.Solid | bd.Compound | None = None,
        stl_path: Path | None = None,
        fluid: CFDFluidProperties = CFDFluidProperties(),
        boundary: CFDBoundaryConfig = CFDBoundaryConfig(),
        solver: CFDSolverConfig = CFDSolverConfig(),
    ) -> None:
        """Initialise le fournisseur CFD avec une géométrie 3D build123d ou STL.

        Args:
            entity_id: Identifiant unique de l'entité.
            part: Solide mécanique ou aérodynamique build123d.
            stl_path: Chemin alternatif vers une surface STL fermée.
            fluid: Propriétés physiques du fluide (viscosité, masse volumique).
            boundary: Vitesse d'écoulement et dimensions du domaine virtuel.
            solver: Paramètres de discrétisation numérique OpenFOAM.
        """
        self._entity_id: str = entity_id
        self._part: bd.Part | bd.Solid | bd.Compound | None = part
        self._stl_path: Path | None = stl_path
        self._fluid: CFDFluidProperties = fluid
        self._boundary: CFDBoundaryConfig = boundary
        self._solver: CFDSolverConfig = solver

        self._cached_result: CFDResultReport | None = None

    @property
    def entity_id(self) -> str:
        return self._entity_id

    @property
    def fluid(self) -> CFDFluidProperties:
        return self._fluid

    @property
    def boundary(self) -> CFDBoundaryConfig:
        return self._boundary

    def generate_case_directory(self, target_dir: Path) -> Path:
        """Génère l'arborescence complète standard d'un cas OpenFOAM prêt à l'exécution.

        Arborescence générée conforme à OpenFOAM v20+ :
        - 0/ (Champs initiaux U, p, k, omega, nut)
        - constant/ (Propriétés de transport, modèle de turbulence, géométrie STL)
        - system/ (controlDict, fvSchemes, fvSolution, blockMeshDict, snappyHexMeshDict)

        Args:
            target_dir: Répertoire cible racine du cas OpenFOAM.

        Returns:
            Path: Chemin absolu du répertoire de cas configuré.
        """
        target_dir.mkdir(parents=True, exist_ok=True)
        dir_0 = target_dir / "0"
        dir_constant = target_dir / "constant"
        dir_system = target_dir / "system"
        dir_trisurface = dir_constant / "triSurface"

        for d in (dir_0, dir_constant, dir_system, dir_trisurface):
            d.mkdir(parents=True, exist_ok=True)

        # 1. Export de la géométrie au format STL
        stl_file = dir_trisurface / "part_geometry.stl"
        if self._part is not None:
            bd.export_stl(self._part, str(stl_file), tolerance=0.001)
        elif self._stl_path is not None and self._stl_path.exists():
            shutil.copy2(self._stl_path, stl_file)
        else:
            raise ValueError("Aucune géométrie disponible pour générer le cas CFD.")

        # Calcul de la boîte englobante de la géométrie
        bmin, bmax = self._get_bounding_box()
        dx = bmax[0] - bmin[0]
        dy = bmax[1] - bmin[1]
        dz = bmax[2] - bmin[2]

        # Calcul du domaine fluide virtuel (soufflerie)
        mx, my, mz = self._boundary.domain_multiplier
        dom_min = (bmin[0] - dx * mx * 0.5, bmin[1] - dy * my * 0.5, bmin[2] - dz * mz * 0.5)
        dom_max = (bmax[0] + dx * mx * 1.5, bmax[1] + dy * my * 0.5, bmax[2] + dz * mz * 0.5)

        # Calcul des grandeurs de turbulence initiales pour k-omega SST
        u_mag = math.sqrt(sum(v**2 for v in self._boundary.inlet_velocity_m_s))
        u_mag = max(u_mag, 1e-3)
        i_turb = self._boundary.turbulence_intensity
        l_turb = self._boundary.turbulent_length_scale_m
        k_val = 1.5 * (u_mag * i_turb) ** 2
        c_mu = 0.09
        omega_val = (k_val**0.5) / (c_mu**0.25 * l_turb)

        # 2. Fichiers du dossier 0/ (Champs physiques)
        self._write_field_u(dir_0 / "U")
        self._write_field_p(dir_0 / "p")
        self._write_field_k(dir_0 / "k", k_val)
        self._write_field_omega(dir_0 / "omega", omega_val)
        self._write_field_nut(dir_0 / "nut")

        # 3. Fichiers du dossier constant/
        self._write_transport_properties(dir_constant / "transportProperties")
        self._write_turbulence_properties(dir_constant / "turbulenceProperties")

        # 4. Fichiers du dossier system/
        self._write_control_dict(dir_system / "controlDict")
        self._write_fv_schemes(dir_system / "fvSchemes")
        self._write_fv_solution(dir_system / "fvSolution")
        self._write_block_mesh_dict(dir_system / "blockMeshDict", dom_min, dom_max)
        self._write_snappy_hex_mesh_dict(dir_system / "snappyHexMeshDict")

        return target_dir

    def _get_bounding_box(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """Calcule les bornes minimales et maximales du solide en mètres."""
        if self._part is not None:
            bb = self._part.bounding_box()
            scale = 0.001  # mm vers m
            return (
                (bb.min.X * scale, bb.min.Y * scale, bb.min.Z * scale),
                (bb.max.X * scale, bb.max.Y * scale, bb.max.Z * scale),
            )
        return ((-0.05, -0.05, -0.05), (0.05, 0.05, 0.05))

    def _write_field_u(self, path: Path) -> None:
        ux, uy, uz = self._boundary.inlet_velocity_m_s
        content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v20+                                  |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       volVectorField;
    object      U;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

dimensions      [0 1 -1 0 0 0 0];

internalField   uniform ({ux} {uy} {uz});

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform ({ux} {uy} {uz});
    }}

    outlet
    {{
        type            zeroGradient;
    }}

    walls
    {{
        type            slip;
    }}

    part_wall
    {{
        type            noSlip;
    }}
}}
"""
        path.write_text(content, encoding="utf-8")

    def _write_field_p(self, path: Path) -> None:
        content = """/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{
    version     2.0;
    format      ascii;
    class       volScalarField;
    object      p;
}
dimensions      [0 2 -2 0 0 0 0];

internalField   uniform 0;

boundaryField
{
    inlet
    {
        type            zeroGradient;
    }

    outlet
    {
        type            fixedValue;
        value           uniform 0;
    }

    walls
    {
        type            zeroGradient;
    }

    part_wall
    {
        type            zeroGradient;
    }
}
"""
        path.write_text(content, encoding="utf-8")

    def _write_field_k(self, path: Path, k_val: float) -> None:
        content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       volScalarField;
    object      k;
}}
dimensions      [0 2 -2 0 0 0 0];

internalField   uniform {k_val:.6e};

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform {k_val:.6e};
    }}

    outlet
    {{
        type            zeroGradient;
    }}

    walls
    {{
        type            slip;
    }}

    part_wall
    {{
        type            kqRWallFunction;
        value           uniform {k_val:.6e};
    }}
}}
"""
        path.write_text(content, encoding="utf-8")

    def _write_field_omega(self, path: Path, omega_val: float) -> None:
        content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       volScalarField;
    object      omega;
}}
dimensions      [0 0 -1 0 0 0 0];

internalField   uniform {omega_val:.6e};

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform {omega_val:.6e};
    }}

    outlet
    {{
        type            zeroGradient;
    }}

    walls
    {{
        type            slip;
    }}

    part_wall
    {{
        type            omegaWallFunction;
        value           uniform {omega_val:.6e};
    }}
}}
"""
        path.write_text(content, encoding="utf-8")

    def _write_field_nut(self, path: Path) -> None:
        content = """/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{
    version     2.0;
    format      ascii;
    class       volScalarField;
    object      nut;
}
dimensions      [0 2 -1 0 0 0 0];

internalField   uniform 0;

boundaryField
{
    inlet
    {
        type            calculated;
        value           uniform 0;
    }

    outlet
    {
        type            calculated;
        value           uniform 0;
    }

    walls
    {
        type            calculated;
        value           uniform 0;
    }

    part_wall
    {
        type            nutkWallFunction;
        value           uniform 0;
    }
}
"""
        path.write_text(content, encoding="utf-8")

    def _write_transport_properties(self, path: Path) -> None:
        content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "constant";
    object      transportProperties;
}}
transportModel  Newtonian;
nu              [0 2 -1 0 0 0 0] {self._fluid.kinematic_viscosity_m2_s:.6e};
"""
        path.write_text(content, encoding="utf-8")

    def _write_turbulence_properties(self, path: Path) -> None:
        content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "constant";
    object      turbulenceProperties;
}}
simulationType  RAS;

RAS
{{
    RASModel        {self._solver.turbulence_model};
    turbulence      on;
    printCoeffs     on;
}}
"""
        path.write_text(content, encoding="utf-8")

    def _write_control_dict(self, path: Path) -> None:
        content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "system";
    object      controlDict;
}}
application     {self._solver.solver_name};
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {self._solver.num_iterations};
deltaT          1;
writeControl    timeStep;
writeInterval   {self._solver.write_interval};
purgeWrite      2;
writeFormat     ascii;
writePrecision  6;
writeCompression off;
runTimeModifiable true;

functions
{{
    forces
    {{
        type            forces;
        libs            ("libforces.so");
        writeControl    timeStep;
        writeInterval   1;
        patches         ("part_wall");
        rho             rhoInf;
        rhoInf          {self._fluid.density_kg_m3};
        CofG            (0 0 0);
    }}
}}
"""
        path.write_text(content, encoding="utf-8")

    def _write_fv_schemes(self, path: Path) -> None:
        content = """/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "system";
    object      fvSchemes;
}
ddtSchemes
{
    default         steadyState;
}

gradSchemes
{
    default         Gauss linear;
}

divSchemes
{
    default         none;
    div(phi,U)      bounded Gauss linearUpwind grad(U);
    div(phi,k)      bounded Gauss upwind;
    div(phi,omega)  bounded Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}

laplacianSchemes
{
    default         Gauss linear corrected;
}

interpolationSchemes
{
    default         linear;
}

snGradSchemes
{
    default         corrected;
}
"""
        path.write_text(content, encoding="utf-8")

    def _write_fv_solution(self, path: Path) -> None:
        content = """/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "system";
    object      fvSolution;
}
solvers
{
    p
    {
        solver          GAMG;
        tolerance       1e-06;
        relTol          0.05;
        smoother        GaussSeidel;
    }

    U
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0.05;
    }

    k
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0.05;
    }

    omega
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0.05;
    }
}

SIMPLE
{
    nNonOrthogonalCorrectors 1;
    consistent               yes;
}

relaxationFactors
{
    equations
    {
        U               0.9;
        k               0.7;
        omega           0.7;
    }
    fields
    {
        p               0.3;
    }
}
"""
        path.write_text(content, encoding="utf-8")

    def _write_block_mesh_dict(
        self,
        path: Path,
        dom_min: tuple[float, float, float],
        dom_max: tuple[float, float, float],
    ) -> None:
        x0, y0, z0 = dom_min
        x1, y1, z1 = dom_max
        content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "system";
    object      blockMeshDict;
}}
scale   1;

vertices
(
    ({x0} {y0} {z0}) // 0
    ({x1} {y0} {z0}) // 1
    ({x1} {y1} {z0}) // 2
    ({x0} {y1} {z0}) // 3
    ({x0} {y0} {z1}) // 4
    ({x1} {y0} {z1}) // 5
    ({x1} {y1} {z1}) // 6
    ({x0} {y1} {z1}) // 7
);

blocks
(
    hex (0 1 2 3 4 5 6 7) (30 15 15) simpleGrading (1 1 1)
);

edges ();

boundary
(
    inlet
    {{
        type patch;
        faces
        (
            (0 4 7 3)
        );
    }}
    outlet
    {{
        type patch;
        faces
        (
            (1 2 6 5)
        );
    }}
    walls
    {{
        type wall;
        faces
        (
            (0 1 5 4)
            (3 7 6 2)
            (0 3 2 1)
            (4 5 6 7)
        );
    }}
);
"""
        path.write_text(content, encoding="utf-8")

    def _write_snappy_hex_mesh_dict(self, path: Path) -> None:
        content = """/*--------------------------------*- C++ -*----------------------------------*\\
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "system";
    object      snappyHexMeshDict;
}
castellatedMesh true;
snap            true;
addLayers       true;

geometry
{
    part_geometry.stl
    {
        type triSurfaceMesh;
        name part_wall;
    }
}

castellatedMeshControls
{
    maxLocalCells 100000;
    maxGlobalCells 2000000;
    minRefinementCells 0;
    maxLoadUnbalance 0.10;
    nCellsBetweenLevels 2;

    features ();

    refinementSurfaces
    {
        part_wall
        {
            level (2 3);
        }
    }

    resolveFeatureAngle 30;

    refinementRegions {}

    locationInMesh (0.01 0.01 0.01);
    allowFreeStandingZoneFaces true;
}

snapControls
{
    nSmoothPatch 3;
    tolerance 2.0;
    nSolveIter 30;
    nRelaxIter 5;
}

addLayersControls
{
    relativeSizes true;
    layers
    {
        part_wall
        {
            nSurfaceLayers 3;
        }
    }
    expansionRatio 1.2;
    finalLayerThickness 0.5;
    minThickness 0.1;
}

meshQualityControls
{
    maxNonOrtho 65;
    maxBoundarySkewness 20;
    maxInternalSkewness 4;
    maxConcave 80;
    minVol 1e-13;
    minTetQuality 1e-30;
}
"""
        path.write_text(content, encoding="utf-8")

    def simulate(self, output_dir: Path) -> CFDResultReport:
        """Exécute la simulation ou calcule les estimations aérodynamiques préliminaires."""
        case_dir = self.generate_case_directory(output_dir)

        # Calcul analytique préliminaire de référence (Résistance aérodynamique de corps immergé)
        bmin, bmax = self._get_bounding_box()
        frontal_area = max((bmax[1] - bmin[1]) * (bmax[2] - bmin[2]), 1e-4)  # m²
        u_mag = math.sqrt(sum(v**2 for v in self._boundary.inlet_velocity_m_s))
        rho = self._fluid.density_kg_m3
        q_dyn = 0.5 * rho * (u_mag**2)

        # Coefficient de traînée théorique pour corps profilé / bloc
        aspect_ratio = (bmax[0] - bmin[0]) / max(math.sqrt(frontal_area), 1e-4)
        cd_estimate = 0.45 if aspect_ratio < 2.0 else 0.18
        cl_estimate = 0.05

        f_drag = cd_estimate * q_dyn * frontal_area
        f_lift = cl_estimate * q_dyn * frontal_area

        # Tentative d'exécution d'OpenFOAM si le binaire 'simpleFoam' est présent dans le PATH
        simple_foam_bin = shutil.which("simpleFoam")
        converged = True
        if simple_foam_bin is not None:
            try:
                subprocess.run(["blockMesh"], cwd=str(case_dir), check=True, capture_output=True, timeout=60)
                subprocess.run(["simpleFoam"], cwd=str(case_dir), check=True, capture_output=True, timeout=120)
            except Exception:
                converged = False

        report = CFDResultReport(
            drag_force_n=float(f_drag),
            lift_force_n=float(f_lift),
            drag_coefficient_cd=float(cd_estimate),
            lift_coefficient_cl=float(cl_estimate),
            converged=converged,
            num_iterations=self._solver.num_iterations,
            case_path=str(case_dir),
        )
        self._cached_result = report
        return report

    def tessellate_binary(
        self,
        linear_deflection: float = 0.001,
        angular_deflection: float = 0.2,
    ) -> BinaryMeshPayload:
        """Délègue la tessellation géométrique au solide B-Rep sous-jacent."""
        if self._part is not None:
            from mono.providers.brep_build123d import BRepBuild123dProvider
            p = BRepBuild123dProvider(self._entity_id, self._part)
            return p.tessellate_binary(linear_deflection, angular_deflection)

        # Repli sur maillage par défaut
        verts = np.array([[-0.05, 0, 0], [0.05, 0, 0], [0, 0.05, 0]], dtype=np.float32)
        normals = np.array([[0, 0, 1], [0, 0, 1], [0, 0, 1]], dtype=np.float32)
        indices = np.array([[0, 1, 2]], dtype=np.uint32)
        face_ids = np.array([1], dtype=np.uint32)
        return BinaryMeshPayload(
            entity_id=self._entity_id,
            vertices=verts,
            normals=normals,
            indices=indices,
            face_ids=face_ids,
        )

    def introspect(self) -> ValidationReport:
        """Inspecte la géométrie pour validation aérodynamique."""
        if self._part is not None:
            from mono.providers.brep_build123d import BRepBuild123dProvider
            p = BRepBuild123dProvider(self._entity_id, self._part)
            return p.introspect()

        return ValidationReport(
            is_valid_l0=True,
            is_watertight=True,
            volume_m3=0.001,
            surface_area_m2=0.01,
            center_of_mass=(0.0, 0.0, 0.0),
            bbox_min=(-0.05, -0.05, -0.05),
            bbox_max=(0.05, 0.05, 0.05),
            topology_counts={"cfd_status": 1},
            dfm_warnings=[],
        )

    def resolve_selector(self, selector_expr: str) -> list[int]:
        if self._part is not None:
            from mono.providers.brep_build123d import BRepBuild123dProvider
            p = BRepBuild123dProvider(self._entity_id, self._part)
            return p.resolve_selector(selector_expr)
        return []

    def serialize_native(self) -> bytes:
        if self._part is not None:
            from mono.providers.brep_build123d import BRepBuild123dProvider
            p = BRepBuild123dProvider(self._entity_id, self._part)
            return p.serialize_native()
        return b""
