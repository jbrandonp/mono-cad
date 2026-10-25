/**
 * Intégrateur du composant WebGL Mol* (MolStar) pour la visualisation
 * macromoléculaire 3D (rubans C-alpha, surfaces, densités, BinaryCIF).
 */

import { Viewer } from 'molstar/lib/apps/viewer/app.js';
import 'molstar/build/viewer/molstar.css';

export class MolstarViewer {
  private viewer: Viewer | null = null;
  private containerId: string;
  private isInitialized: boolean = false;

  constructor(containerId: string) {
    this.containerId = containerId;
  }

  public async init(): Promise<void> {
    if (this.isInitialized && this.viewer) return;
    const container = document.getElementById(this.containerId);
    if (!container) {
      console.warn(`Conteneur Mol* '#${this.containerId}' introuvable.`);
      return;
    }

    try {
      this.viewer = await Viewer.create(container, {
        layoutIsExpanded: false,
        layoutShowControls: true,
        layoutShowRemoteState: false,
        layoutShowSequence: true,
        layoutShowLog: false,
        viewportShowExpand: false,
        viewportShowSelectionMode: false,
        viewportShowAnimation: true,
        illumination: true,
      });
      this.isInitialized = true;
      console.log('Mol* Viewer initialisé avec succès.');
    } catch (err) {
      console.error('Erreur initialisation Mol*:', err);
    }
  }

  public async loadFromUrl(url: string, format: 'bcif' | 'cif' | 'pdb' = 'bcif'): Promise<void> {
    if (!this.viewer) {
      await this.init();
    }
    if (!this.viewer) return;

    try {
      const isBinary = format === 'bcif';
      const trajFormat = format === 'bcif' || format === 'cif' ? 'mmcif' : 'pdb';
      await this.viewer.loadStructureFromUrl(url, trajFormat, isBinary);
    } catch (err) {
      console.error('Erreur chargement structure Mol*:', err);
    }
  }

  public async loadPdbString(pdbContent: string): Promise<void> {
    if (!this.viewer) {
      await this.init();
    }
    if (!this.viewer) return;

    try {
      await this.viewer.loadPdb(pdbContent);
    } catch (err) {
      console.error('Erreur chargement PDB Mol*:', err);
    }
  }

  public getViewerInstance(): Viewer | null {
    return this.viewer;
  }
}
