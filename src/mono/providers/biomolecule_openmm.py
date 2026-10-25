"""Moteur de dynamique moléculaire OpenMM pour macromolecules biologiques 3D.

Permet de simuler le mouvement thermique réaliste des atomes d'une protéine ou d'un acide nucléique
dans le temps (intégration de Langevin / Verlet) sous champ de force tout-atome Amber ou sous
modèle de réseau élastique harmonique (Elastic Network Model, ENM).
Calcule l'énergie potentielle, la température thermodynamique instantanée et la déviation
quadratique moyenne (RMSD) image par image pour la lecture fluide de trajectoires.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import math
from typing import List, Literal, Sequence, Tuple
import numpy as np
import numpy.typing as npt
import openmm
import openmm.app as app
from openmm import unit

from mono.providers.biomolecule_gemmi import BiomoleculeProvider


@dataclass(frozen=True)
class TrajectoryFrame:
    """Instantané temporel (frame) de la trajectoire moléculaire dynamique."""

    frame_index: int
    time_ps: float
    potential_energy_kj_mol: float
    kinetic_energy_kj_mol: float
    temperature_k: float
    rmsd_angstrom: float
    positions: npt.NDArray[np.float32]  # Coordonnées (N, 3) en Angströms

    def to_dict(self) -> dict[str, object]:
        """Convertit l'instantané en dictionnaire sérialisable JSON."""
        return {
            "frame_index": self.frame_index,
            "time_ps": round(self.time_ps, 3),
            "potential_energy_kj_mol": round(self.potential_energy_kj_mol, 2),
            "kinetic_energy_kj_mol": round(self.kinetic_energy_kj_mol, 2),
            "temperature_k": round(self.temperature_k, 2),
            "rmsd_angstrom": round(self.rmsd_angstrom, 3),
            "num_atoms": int(self.positions.shape[0]),
        }


@dataclass
class OpenMMSimulationResult:
    """Résultat consolidé d'une simulation dynamique OpenMM."""

    success: bool
    method: str
    total_steps: int
    time_step_fs: float
    initial_energy_kj_mol: float
    final_energy_kj_mol: float
    mean_rmsd_angstrom: float
    max_rmsd_angstrom: float
    frames: list[TrajectoryFrame] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Convertit le résumé de simulation en dictionnaire JSON."""
        return {
            "success": self.success,
            "method": self.method,
            "total_steps": self.total_steps,
            "time_step_fs": self.time_step_fs,
            "initial_energy_kj_mol": round(self.initial_energy_kj_mol, 2),
            "final_energy_kj_mol": round(self.final_energy_kj_mol, 2),
            "mean_rmsd_angstrom": round(self.mean_rmsd_angstrom, 3),
            "max_rmsd_angstrom": round(self.max_rmsd_angstrom, 3),
            "frame_count": len(self.frames),
            "frames_summary": [f.to_dict() for f in self.frames],
        }


class OpenMMSimulator:
    """Solveur et simulateur de dynamique moléculaire OpenMM.

    Exécute l'équilibrage énergétique, la minimisation du potentiel et la simulation de Langevin
    à température contrôlée pour reproduire le comportement biophysique réel des macromolécules.
    """

    def __init__(
        self,
        pdb_content: str,
        temperature_k: float = 300.0,
        time_step_fs: float = 2.0,
        friction_per_ps: float = 1.0,
    ) -> None:
        """Initialise le simulateur avec le contenu d'un fichier PDB et les conditions thermodynamiques."""
        self.pdb_content: str = pdb_content
        self.temperature_k: float = float(temperature_k)
        self.time_step_fs: float = float(time_step_fs)
        self.friction_per_ps: float = float(friction_per_ps)

        self.initial_positions: npt.NDArray[np.float32] = np.zeros((0, 3), dtype=np.float32)
        self.atom_elements: list[str] = []
        self.system: openmm.System | None = None
        self.simulation: app.Simulation | None = None
        self.is_enm_fallback: bool = False

        self._parse_and_setup()

    def _parse_and_setup(self) -> None:
        """Construit le système physique OpenMM via champ de force Amber ou repli ENM."""
        # 1. Tentative tout-atome Amber avec app.PDBFile
        try:
            pdb_io = io.StringIO(self.pdb_content)
            pdb_obj = app.PDBFile(pdb_io)
            ff = app.ForceField("amber14-all.xml")
            modeller = app.Modeller(pdb_obj.topology, pdb_obj.positions)

            # Ajout des atomes d'hydrogène nécessaires au champ de force
            modeller.addHydrogens(ff)

            system = ff.createSystem(
                modeller.topology,
                nonbondedMethod=app.NoCutoff,
                constraints=app.HBonds,
            )

            dt = (self.time_step_fs * 1e-3) * unit.picoseconds
            integrator = openmm.LangevinMiddleIntegrator(
                self.temperature_k * unit.kelvin,
                self.friction_per_ps / unit.picoseconds,
                dt,
            )

            simulation = app.Simulation(modeller.topology, system, integrator)
            simulation.context.setPositions(modeller.positions)

            self.system = system
            self.simulation = simulation
            raw_pos = simulation.context.getState(getPositions=True).getPositions(asNumpy=True)
            self.initial_positions = np.array(raw_pos.value_in_unit(unit.angstrom), dtype=np.float32)
            self.atom_elements = [atom.element.symbol if atom.element else "C" for atom in modeller.topology.atoms()]
            self.is_enm_fallback = False
            return
        except Exception:
            # En cas de résidus non standard, d'extrémités sans cap ou d'atomes manquants,
            # bascule transparente vers le modèle harmonique élastique (ENM)
            self._setup_elastic_network_model()

    def _setup_elastic_network_model(self) -> None:
        """Construit un modèle élastique harmonique (ENM) universel basé sur les coordonnées PDB."""
        provider = BiomoleculeProvider(pdb_content=self.pdb_content)
        base_positions = provider.get_atom_positions()

        if len(base_positions) == 0:
            raise ValueError("Impossible d'initialiser OpenMM : aucun atome détecté dans le PDB.")

        n_atoms = len(base_positions)
        self.initial_positions = base_positions.copy()

        # Système physique avec particules
        system = openmm.System()
        masses: list[float] = []

        model = provider.structure[0]
        elements: list[str] = []
        for chain in model:
            for res in chain:
                for atom in res:
                    elem = atom.element.name.upper()
                    elements.append(elem)
                    # Masse approximative en Daltons
                    m = 12.0
                    if elem == "H":
                        m = 1.008
                    elif elem == "N":
                        m = 14.007
                    elif elem == "O":
                        m = 15.999
                    elif elem == "S":
                        m = 32.065
                    elif elem == "P":
                        m = 30.974
                    masses.append(m)
                    system.addParticle(m)

        self.atom_elements = elements

        # Ressorts harmoniques pour les liaisons covalentes et contacts proches (< 8.0 Å)
        force_bonds = openmm.HarmonicBondForce()
        cutoff_nm = 0.80  # 8.0 Angströms

        for i in range(n_atoms):
            p1 = base_positions[i]
            for j in range(i + 1, n_atoms):
                p2 = base_positions[j]
                dist_angstrom = float(np.linalg.norm(p1 - p2))
                dist_nm = dist_angstrom * 0.1

                if dist_nm <= cutoff_nm:
                    # Raideur : 40000 kJ/(mol*nm^2) pour les liaisons courtes, 5000 pour les contacts lointains
                    k = 40000.0 if dist_angstrom < 2.0 else 5000.0
                    force_bonds.addBond(
                        i,
                        j,
                        dist_nm * unit.nanometer,
                        k * unit.kilojoules_per_mole / (unit.nanometer ** 2),
                    )

        system.addForce(force_bonds)

        dt = (self.time_step_fs * 1e-3) * unit.picoseconds
        integrator = openmm.LangevinMiddleIntegrator(
            self.temperature_k * unit.kelvin,
            self.friction_per_ps / unit.picoseconds,
            dt,
        )

        # Création du contexte OpenMM
        context = openmm.Context(system, integrator)
        openmm_pos = [
            openmm.Vec3(float(base_positions[i, 0]) * 0.1, float(base_positions[i, 1]) * 0.1, float(base_positions[i, 2]) * 0.1)
            for i in range(n_atoms)
        ]
        context.setPositions(openmm_pos)

        # Simulation dummy pour encapsuler le contexte
        class MinimalSim:
            def __init__(self, ctx: openmm.Context, integ: openmm.Integrator) -> None:
                self.context = ctx
                self.integrator = integ

            def step(self, n: int) -> None:
                self.integrator.step(n)

            def minimizeEnergy(self, maxIterations: int = 50) -> None:
                openmm.LocalEnergyMinimizer.minimize(self.context, maxIterations=maxIterations)

        self.system = system
        self.simulation = MinimalSim(context, integrator)  # type: ignore[assignment]
        self.is_enm_fallback = True

    def minimize_energy(self, max_iterations: int = 50) -> float:
        """Minimise l'énergie potentielle du système pour détendre les tensions stériques initiales."""
        if self.simulation is None:
            return 0.0

        self.simulation.minimizeEnergy(maxIterations=max_iterations)
        state = self.simulation.context.getState(getEnergy=True)
        return float(state.getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole))

    def compute_rmsd(self, current_positions: npt.NDArray[np.float32]) -> float:
        """Calcule la déviation quadratique moyenne (RMSD en Angströms) par rapport à la structure de départ."""
        if len(self.initial_positions) == 0 or len(current_positions) != len(self.initial_positions):
            return 0.0

        diff = current_positions - self.initial_positions
        sq_dist = np.sum(diff * diff, axis=1)
        mean_sq = float(np.mean(sq_dist))
        return float(math.sqrt(max(mean_sq, 0.0)))

    def simulate(
        self,
        total_steps: int = 200,
        report_interval: int = 20,
    ) -> OpenMMSimulationResult:
        """Exécute la dynamique moléculaire temporelle et échantillonne les frames successives."""
        if self.simulation is None:
            return OpenMMSimulationResult(
                success=False,
                method="None",
                total_steps=0,
                time_step_fs=self.time_step_fs,
                initial_energy_kj_mol=0.0,
                final_energy_kj_mol=0.0,
                mean_rmsd_angstrom=0.0,
                max_rmsd_angstrom=0.0,
                frames=[],
            )

        # 1. Énergie initiale
        init_state = self.simulation.context.getState(getEnergy=True, getPositions=True)
        init_e = float(init_state.getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole))

        frames: list[TrajectoryFrame] = []
        n_cycles = max(1, total_steps // report_interval)

        # Frame 0 (initiale)
        raw_pos = init_state.getPositions(asNumpy=True).value_in_unit(unit.angstrom)
        pos0 = np.array(raw_pos, dtype=np.float32)
        frames.append(
            TrajectoryFrame(
                frame_index=0,
                time_ps=0.0,
                potential_energy_kj_mol=init_e,
                kinetic_energy_kj_mol=float(init_state.getKineticEnergy().value_in_unit(unit.kilojoules_per_mole)),
                temperature_k=self.temperature_k,
                rmsd_angstrom=0.0,
                positions=pos0,
            )
        )

        # 2. Cycles de dynamique moléculaire
        step_dt_ps = (self.time_step_fs * 1e-3)
        current_time_ps = 0.0

        for cycle in range(1, n_cycles + 1):
            self.simulation.step(report_interval)
            current_time_ps += (report_interval * step_dt_ps)

            state = self.simulation.context.getState(getEnergy=True, getPositions=True)
            pot_e = float(state.getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole))
            kin_e = float(state.getKineticEnergy().value_in_unit(unit.kilojoules_per_mole))

            # Calcul de la température instantanée à partir de l'énergie cinétique T = 2 * E_k / (3 * N * k_B)
            # 1 kJ/mol = 1000 J/mol. k_B * N_A = R = 8.314 J/(mol*K)
            n_particles = max(1, len(self.atom_elements))
            dof = max(1, 3 * n_particles - 6)
            temp_calc = (2.0 * kin_e * 1000.0) / (dof * 8.314462618)

            pos_cycle = np.array(state.getPositions(asNumpy=True).value_in_unit(unit.angstrom), dtype=np.float32)
            rmsd_val = self.compute_rmsd(pos_cycle)

            frames.append(
                TrajectoryFrame(
                    frame_index=cycle,
                    time_ps=current_time_ps,
                    potential_energy_kj_mol=pot_e,
                    kinetic_energy_kj_mol=kin_e,
                    temperature_k=temp_calc,
                    rmsd_angstrom=rmsd_val,
                    positions=pos_cycle,
                )
            )

        rmsds = [f.rmsd_angstrom for f in frames]
        final_e = frames[-1].potential_energy_kj_mol if frames else init_e

        return OpenMMSimulationResult(
            success=True,
            method="ENM_Harmonic" if self.is_enm_fallback else "Amber14_AllAtom",
            total_steps=total_steps,
            time_step_fs=self.time_step_fs,
            initial_energy_kj_mol=init_e,
            final_energy_kj_mol=final_e,
            mean_rmsd_angstrom=float(np.mean(rmsds)),
            max_rmsd_angstrom=float(np.max(rmsds)),
            frames=frames,
        )
