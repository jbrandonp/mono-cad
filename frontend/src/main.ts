import { CadViewport, SceneEntityInfo, TransformMode } from './viewport.ts';
import { MolstarViewer } from './molstar_viewer.ts';

interface ParameterDef {
  value: number;
  min: number;
  max: number;
  unit: string;
  label: string;
}

interface BinaryMeshHeader {
  type: 'binary_mesh_header';
  entity_id: string;
  vertex_count: number;
  triangle_count: number;
  byte_size: number;
}

interface SceneSummary {
  version: string;
  parameters: Record<string, ParameterDef>;
  entities_count: number;
  entities: Record<string, {
    is_valid_l0: boolean;
    volume_m3: number;
    topology: Record<string, number>;
  }>;
}

class MonoApp {
  private viewport: CadViewport;
  private socket: WebSocket | null = null;
  private lastHeader: BinaryMeshHeader | null = null;
  private parametersContainer: HTMLElement;
  private statusElement: HTMLElement;
  private statManifold: HTMLElement;
  private statVolume: HTMLElement;
  private statTriangles: HTMLElement;
  private statVertices: HTMLElement;
  private isTextileRunning: boolean = false;
  private textileAnimationId: number | null = null;
  private statTextileStatus: HTMLElement | null = null;
  private molstarViewer: MolstarViewer;
  private activeView: 'cad' | 'molstar' = 'cad';
  private isTrajectoryPlaying: boolean = false;
  private trajectoryPlaybackTimer: number | null = null;
  private totalTrajectoryFrames: number = 0;
  private currentTrajectoryFrame: number = 0;
  private statBioEnergy: HTMLElement | null = null;
  private labelFrame: HTMLElement | null = null;
  private labelRmsd: HTMLElement | null = null;
  private sliderTrajectory: HTMLInputElement | null = null;
  private lastTechDrawSvg: string = '';
  private sceneTreePanel: HTMLElement | null = null;
  private sceneTreeList: HTMLElement | null = null;
  private treeEntityCount: HTMLElement | null = null;
  private btnToolOrbit: HTMLElement | null = null;
  private btnToolTranslate: HTMLElement | null = null;
  private btnToolRotate: HTMLElement | null = null;
  private btnToolScale: HTMLElement | null = null;
  private btnToggleShadows: HTMLElement | null = null;
  private btnToggleTree: HTMLElement | null = null;

  constructor() {
    const canvas = document.getElementById('cad-canvas') as HTMLCanvasElement;
    this.viewport = new CadViewport(canvas);

    this.parametersContainer = document.getElementById('parameters-container')!;
    this.statusElement = document.getElementById('connection-status')!;
    this.statManifold = document.getElementById('stat-manifold')!;
    this.statVolume = document.getElementById('stat-volume')!;
    this.statTriangles = document.getElementById('stat-triangles')!;
    this.statVertices = document.getElementById('stat-vertices')!;
    this.statTextileStatus = document.getElementById('stat-textile-status');
    this.molstarViewer = new MolstarViewer('molstar-container');
    this.statBioEnergy = document.getElementById('stat-bio-energy');
    this.labelFrame = document.getElementById('label-frame');
    this.labelRmsd = document.getElementById('label-rmsd');
    this.sliderTrajectory = document.getElementById('slider-trajectory') as HTMLInputElement | null;

    this.sceneTreePanel = document.getElementById('scene-tree-panel');
    this.sceneTreeList = document.getElementById('scene-tree-list');
    this.treeEntityCount = document.getElementById('tree-entity-count');
    this.btnToolOrbit = document.getElementById('btn-tool-orbit');
    this.btnToolTranslate = document.getElementById('btn-tool-translate');
    this.btnToolRotate = document.getElementById('btn-tool-rotate');
    this.btnToolScale = document.getElementById('btn-tool-scale');
    this.btnToggleShadows = document.getElementById('btn-toggle-shadows');
    this.btnToggleTree = document.getElementById('btn-toggle-tree');

    this.viewport.onEntitiesChanged = (entities: SceneEntityInfo[]) => {
      this.renderSceneTree(entities);
    };

    this.viewport.onEntitySelected = (selectedId: string | null) => {
      this.updateTreeSelection(selectedId);
    };

    this.setupUIHandlers();
    this.connectWebSocket();

    // Détection de l'environnement Tauri Desktop natif
    if (typeof (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ !== 'undefined') {
      const desktopBadge = document.getElementById('desktop-native-badge');
      if (desktopBadge) {
        desktopBadge.style.display = 'inline-block';
      }
    }
  }

  private setupUIHandlers(): void {
    // Contrôles du Gizmo 3D et modes de transformation
    this.btnToolOrbit?.addEventListener('click', () => {
      this.viewport.setTransformMode('none');
      this.updateToolbarActiveMode('none');
    });

    this.btnToolTranslate?.addEventListener('click', () => {
      this.viewport.setTransformMode('translate');
      this.updateToolbarActiveMode('translate');
    });

    this.btnToolRotate?.addEventListener('click', () => {
      this.viewport.setTransformMode('rotate');
      this.updateToolbarActiveMode('rotate');
    });

    this.btnToolScale?.addEventListener('click', () => {
      this.viewport.setTransformMode('scale');
      this.updateToolbarActiveMode('scale');
    });

    // Bascule d'éclairage PBR et ombres douces
    this.btnToggleShadows?.addEventListener('click', () => {
      const active = this.viewport.toggleShadows();
      this.btnToggleShadows?.classList.toggle('active', active);
    });

    // Bascule du panneau Arborescence de scène
    this.btnToggleTree?.addEventListener('click', () => {
      const isHidden = this.sceneTreePanel?.classList.toggle('hidden');
      this.btnToggleTree?.classList.toggle('active', !isHidden);
    });

    // Actions globales de l'arborescence
    document.getElementById('btn-tree-show-all')?.addEventListener('click', () => {
      const list = this.viewport.getEntityList();
      for (const item of list) {
        this.viewport.setEntityVisibility(item.id, true);
      }
    });

    document.getElementById('btn-tree-hide-all')?.addEventListener('click', () => {
      const list = this.viewport.getEntityList();
      for (const item of list) {
        this.viewport.setEntityVisibility(item.id, false);
      }
    });

    document.getElementById('btn-tree-clear-all')?.addEventListener('click', () => {
      if (confirm('Voulez-vous vraiment vider toutes les entités 3D de la scène ?')) {
        this.viewport.clearScene();
      }
    });

    const btnQuadView = document.getElementById('btn-quad-view');
    if (btnQuadView) {
      btnQuadView.addEventListener('click', () => {
        window.open('http://127.0.0.1:8000/api/scene/quad-view', '_blank');
      });
    }

    const btnUndo = document.getElementById('btn-undo');
    if (btnUndo) {
      btnUndo.addEventListener('click', () => {
        this.sendMessage({ type: 'undo' });
      });
    }

    const btnRedo = document.getElementById('btn-redo');
    if (btnRedo) {
      btnRedo.addEventListener('click', () => {
        this.sendMessage({ type: 'redo' });
      });
    }

    const btnFea = document.getElementById('btn-fea-simulate');
    if (btnFea) {
      btnFea.addEventListener('click', () => {
        btnFea.textContent = '⏳ CalculiX en cours...';
        this.sendMessage({
          type: 'run_fea',
          entity_id: 'part_001',
          young_modulus_gpa: 70.0,
          yield_strength_mpa: 275.0,
          force_z: -2500.0,
        });
      });
    }

    // Contrôles Atelier Textile GPU XPBD
    const btnTextileToggle = document.getElementById('btn-textile-toggle');
    if (btnTextileToggle) {
      btnTextileToggle.addEventListener('click', () => {
        if (!this.isTextileRunning) {
          this.isTextileRunning = true;
          btnTextileToggle.textContent = '⏸ Pause Simulation Textile';
          btnTextileToggle.style.background = 'linear-gradient(135deg, #ef4444, #dc2626)';
          if (this.statTextileStatus) {
            this.statTextileStatus.textContent = 'Actif (60 FPS)';
            this.statTextileStatus.className = 'stat-value highlight';
          }
          // Initialisation côté serveur si nécessaire
          this.sendMessage({
            type: 'init_textile',
            entity_id: 'textile_001',
            res_x: 32,
            res_y: 32,
            spacing: 0.03,
            center_y: 1.2,
            pin: 'top',
          });
          this.runTextileLoop();
        } else {
          this.isTextileRunning = false;
          if (this.textileAnimationId !== null) {
            cancelAnimationFrame(this.textileAnimationId);
            this.textileAnimationId = null;
          }
          btnTextileToggle.textContent = '▶ Reprendre Simulation Textile';
          btnTextileToggle.style.background = 'linear-gradient(135deg, #10b981, #059669)';
          if (this.statTextileStatus) {
            this.statTextileStatus.textContent = 'En pause';
            this.statTextileStatus.className = 'stat-value';
          }
        }
      });
    }

    const btnTextileWind = document.getElementById('btn-textile-wind');
    if (btnTextileWind) {
      btnTextileWind.addEventListener('click', () => {
        const vz = 2.0 + Math.random() * 3.5;
        const vx = (Math.random() - 0.5) * 3.0;
        this.sendMessage({
          type: 'set_textile_wind',
          entity_id: 'textile_001',
          vx,
          vy: 0.5,
          vz,
        });
      });
    }

    const btnTextileSphere = document.getElementById('btn-textile-sphere');
    if (btnTextileSphere) {
      btnTextileSphere.addEventListener('click', () => {
        this.sendMessage({
          type: 'add_textile_sphere',
          entity_id: 'textile_001',
          cx: 0.0,
          cy: 0.45,
          cz: 0.0,
          radius: 0.22,
        });
      });
    }

    const btnTextileDrapeBox = document.getElementById('btn-textile-drape-box');
    if (btnTextileDrapeBox) {
      btnTextileDrapeBox.addEventListener('click', () => {
        this.sendMessage({
          type: 'drape_on_brep_sdf',
          entity_id: 'textile_001',
          solid_entity_id: 'part_001',
        });
      });
    }

    const btnTextileUnpin = document.getElementById('btn-textile-unpin');
    if (btnTextileUnpin) {
      btnTextileUnpin.addEventListener('click', () => {
        this.sendMessage({
          type: 'unpin_textile',
          entity_id: 'textile_001',
        });
      });
    }

    // --- Contrôles Biologie Moléculaire (Gemmi / Mol* / OpenMM) ---
    const btnLoadProtein = document.getElementById('btn-load-protein');
    const inputPdbCode = document.getElementById('input-pdb-code') as HTMLInputElement | null;
    const selectBioMode = document.getElementById('select-bio-mode') as HTMLSelectElement | null;
    if (btnLoadProtein) {
      btnLoadProtein.addEventListener('click', () => {
        const pdbCode = inputPdbCode ? inputPdbCode.value.trim().toUpperCase() : '1CRN';
        const mode = selectBioMode ? selectBioMode.value : 'ribbon';
        btnLoadProtein.textContent = '⏳ Chargement PDB...';
        this.sendMessage({
          type: 'load_biomolecule',
          entity_id: 'protein_001',
          pdb_code: pdbCode,
          mode: mode,
        });
      });
    }

    const btnDownloadPdb = document.getElementById('btn-download-pdb');
    if (btnDownloadPdb) {
      btnDownloadPdb.addEventListener('click', () => {
        window.open('http://127.0.0.1:8000/api/biomolecule/pdb?entity_id=protein_001', '_blank');
      });
    }

    const btnToggleView = document.getElementById('btn-toggle-viewport-view');
    const cadView = document.getElementById('cad-view');
    const molstarView = document.getElementById('molstar-view');
    const hudCamera = document.getElementById('hud-camera-mode');

    if (btnToggleView && cadView && molstarView) {
      btnToggleView.addEventListener('click', async () => {
        if (this.activeView === 'cad') {
          this.activeView = 'molstar';
          cadView.style.display = 'none';
          molstarView.style.display = 'block';
          btnToggleView.textContent = '🔄 Vue CAO';
          if (hudCamera) hudCamera.textContent = 'MOL* BIO-WEBGL';

          await this.molstarViewer.init();
          await this.molstarViewer.loadFromUrl('http://127.0.0.1:8000/api/biomolecule/bcif', 'bcif');
        } else {
          this.activeView = 'cad';
          molstarView.style.display = 'none';
          cadView.style.display = 'block';
          btnToggleView.textContent = '🔄 Vue Mol*';
          if (hudCamera) hudCamera.textContent = 'PERSPECTIVE / RTC FLOAT64';
        }
      });
    }

    const btnOpenmmSim = document.getElementById('btn-openmm-sim');
    if (btnOpenmmSim) {
      btnOpenmmSim.addEventListener('click', () => {
        btnOpenmmSim.textContent = '⏳ OpenMM en cours...';
        this.sendMessage({
          type: 'run_biomolecule_simulation',
          entity_id: 'protein_001',
          total_steps: 100,
          report_interval: 10,
          temperature_k: 300.0,
        });
      });
    }

    const btnRamachandran = document.getElementById('btn-ramachandran');
    if (btnRamachandran) {
      btnRamachandran.addEventListener('click', async () => {
        try {
          const resp = await fetch('http://127.0.0.1:8000/api/biomolecule/ramachandran?entity_id=protein_001');
          const data = (await resp.json()) as {
            ramachandran_points: Array<{ res_name: string; res_num: number; phi: number; psi: number; region: string }>;
            disulfide_bridges: Array<{ res1: number; res2: number; distance: number }>;
          };
          const count = data.ramachandran_points.length;
          const disulf = data.disulfide_bridges.length;
          alert(`Analyse de conformation Ramachandran :\n• Résidus analysés : ${count}\n• Ponts disulfures S-S : ${disulf}\n• Détail : ${data.ramachandran_points.map(p => `${p.res_name}${p.res_num}: φ=${p.phi}°, ψ=${p.psi}° [${p.region}]`).slice(0, 5).join('\n')}`);
        } catch (err) {
          alert(`Erreur Ramachandran : ${err}`);
        }
      });
    }

    if (this.sliderTrajectory) {
      this.sliderTrajectory.addEventListener('input', () => {
        if (!this.sliderTrajectory) return;
        const frameIdx = parseInt(this.sliderTrajectory.value, 10);
        this.currentTrajectoryFrame = frameIdx;
        this.sendMessage({
          type: 'step_biomolecule_dynamics',
          entity_id: 'protein_001',
          frame_index: frameIdx,
        });
      });
    }

    const btnPlayTraj = document.getElementById('btn-play-trajectory');
    if (btnPlayTraj) {
      btnPlayTraj.addEventListener('click', () => {
        if (!this.isTrajectoryPlaying) {
          if (this.totalTrajectoryFrames <= 1) {
            alert('Veuillez d\'abord lancer une simulation OpenMM.');
            return;
          }
          this.isTrajectoryPlaying = true;
          btnPlayTraj.textContent = '⏸ Pause';
          btnPlayTraj.style.background = 'linear-gradient(135deg, #ef4444, #dc2626)';
          this.playTrajectoryLoop();
        } else {
          this.isTrajectoryPlaying = false;
          if (this.trajectoryPlaybackTimer !== null) {
            clearInterval(this.trajectoryPlaybackTimer);
            this.trajectoryPlaybackTimer = null;
          }
          btnPlayTraj.textContent = '▶ Lecture';
          btnPlayTraj.style.background = '';
        }
      });
    }

    const btnDownloadBcif = document.getElementById('btn-download-bcif');
    if (btnDownloadBcif) {
      btnDownloadBcif.addEventListener('click', () => {
        window.open('http://127.0.0.1:8000/api/biomolecule/bcif?entity_id=protein_001', '_blank');
      });
    }

    // --- Contrôles FAO (CAM) & Impression 3D (Phase 8) ---
    const btnGenerateCam = document.getElementById('btn-generate-cam');
    const selectCamStrategy = document.getElementById('select-cam-strategy') as HTMLSelectElement | null;
    const selectCamFlavor = document.getElementById('select-cam-flavor') as HTMLSelectElement | null;

    if (btnGenerateCam) {
      btnGenerateCam.addEventListener('click', () => {
        const strat = selectCamStrategy ? selectCamStrategy.value : 'drop_cutter_surface';
        btnGenerateCam.textContent = '⏳ Calcul FAO...';
        this.sendMessage({
          type: 'generate_cam_toolpath',
          entity_id: 'part_001',
          strategy: strat,
          tool_diameter_mm: 10.0,
          feedrate_mm_min: 3600.0,
        });
      });
    }

    const btnDownloadCamGcode = document.getElementById('btn-download-cam-gcode');
    if (btnDownloadCamGcode) {
      btnDownloadCamGcode.addEventListener('click', () => {
        const flv = selectCamFlavor ? selectCamFlavor.value : 'fanuc';
        window.open(`http://127.0.0.1:8000/api/cam/gcode?entity_id=part_001_cam_toolpath&flavor=${flv}`, '_blank');
      });
    }

    const btnSliceAdditive = document.getElementById('btn-slice-additive');
    if (btnSliceAdditive) {
      btnSliceAdditive.addEventListener('click', () => {
        btnSliceAdditive.textContent = '⏳ Tranchage 3D...';
        this.sendMessage({
          type: 'slice_additive_part',
          entity_id: 'part_001',
          layer_height_mm: 0.20,
          infill_percent: 20,
        });
      });
    }

    const btnDownloadSlicerGcode = document.getElementById('btn-download-slicer-gcode');
    if (btnDownloadSlicerGcode) {
      btnDownloadSlicerGcode.addEventListener('click', () => {
        window.open('http://127.0.0.1:8000/api/slicer/gcode?entity_id=part_001', '_blank');
      });
    }

    const btnAuditDfam = document.getElementById('btn-audit-dfam');
    if (btnAuditDfam) {
      btnAuditDfam.addEventListener('click', async () => {
        try {
          const resp = await fetch('http://127.0.0.1:8000/api/slicer/audit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entity_id: 'part_001', max_overhang_angle_deg: 45.0, nozzle_diameter_mm: 0.4 }),
          });
          const data = (await resp.json()) as {
            overhang_ratio: number;
            max_overhang_deg: number;
            fits_build_plate: boolean;
            is_manufacturable: boolean;
            warnings: string[];
          };
          alert(`Audit DfAM Fabrication Additive :\n• Conforme plateau : ${data.fits_build_plate ? 'OUI' : 'NON'}\n• Ratio surplombs : ${(data.overhang_ratio * 100).toFixed(1)}%\n• Angle max : ${data.max_overhang_deg}°\n• Fabricabilité : ${data.is_manufacturable ? 'CONFORME' : 'SUPPORTS REQUIS'}\n• Alertes : ${data.warnings.join('\n') || 'Aucune alerte'}`);
        } catch (err) {
          alert(`Erreur audit DfAM : ${err}`);
        }
      });
    }

    // --- Contrôles Plans Techniques 2D & Conteneur .mono (Phase 9) ---
    const btnGenerateTechDraw = document.getElementById('btn-generate-techdraw');
    const selectTechdrawFormat = document.getElementById('select-techdraw-format') as HTMLSelectElement | null;
    const selectTechdrawOrient = document.getElementById('select-techdraw-orient') as HTMLSelectElement | null;
    const inputTechdrawTitle = document.getElementById('input-techdraw-title') as HTMLInputElement | null;
    const inputTechdrawDwg = document.getElementById('input-techdraw-dwg') as HTMLInputElement | null;

    if (btnGenerateTechDraw) {
      btnGenerateTechDraw.addEventListener('click', () => {
        btnGenerateTechDraw.textContent = '⏳ Calcul HLR...';
        this.sendMessage({
          type: 'generate_techdraw',
          entity_id: 'part_001',
          sheet_format: selectTechdrawFormat ? selectTechdrawFormat.value : 'A3',
          orientation: selectTechdrawOrient ? selectTechdrawOrient.value : 'landscape',
          title: inputTechdrawTitle ? inputTechdrawTitle.value : 'PIÈCE INDUSTRIELLE',
          drawing_number: inputTechdrawDwg ? inputTechdrawDwg.value : 'DWG-1001',
        });
      });
    }

    const btnPreviewTechdraw = document.getElementById('btn-preview-techdraw');
    const modalTechdraw = document.getElementById('techdraw-modal');
    const containerSvg = document.getElementById('techdraw-svg-container');
    const btnCloseModal = document.getElementById('btn-modal-close-techdraw');
    const btnModalDownloadSvg = document.getElementById('btn-modal-download-svg');

    if (btnPreviewTechdraw) {
      btnPreviewTechdraw.addEventListener('click', () => {
        if (!this.lastTechDrawSvg) {
          alert("Veuillez d'abord générer le plan technique 2D.");
          return;
        }
        if (modalTechdraw && containerSvg) {
          containerSvg.innerHTML = this.lastTechDrawSvg;
          modalTechdraw.style.display = 'flex';
        }
      });
    }

    if (btnCloseModal && modalTechdraw) {
      btnCloseModal.addEventListener('click', () => {
        modalTechdraw.style.display = 'none';
      });
    }

    if (btnModalDownloadSvg) {
      btnModalDownloadSvg.addEventListener('click', () => {
        if (!this.lastTechDrawSvg) return;
        const blob = new Blob([this.lastTechDrawSvg], { type: 'image/svg+xml' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'plan_technique.svg';
        a.click();
        URL.revokeObjectURL(url);
      });
    }

    const btnDownloadPdf = document.getElementById('btn-download-techdraw-pdf');
    if (btnDownloadPdf) {
      btnDownloadPdf.addEventListener('click', () => {
        window.open('http://127.0.0.1:8000/api/techdraw/pdf?entity_id=part_001_techdraw', '_blank');
      });
    }

    const btnModalDownloadPdf = document.getElementById('btn-modal-download-pdf');
    if (btnModalDownloadPdf) {
      btnModalDownloadPdf.addEventListener('click', () => {
        window.open('http://127.0.0.1:8000/api/techdraw/pdf?entity_id=part_001_techdraw', '_blank');
      });
    }

    const btnDownloadDxf = document.getElementById('btn-download-techdraw-dxf');
    if (btnDownloadDxf) {
      btnDownloadDxf.addEventListener('click', () => {
        window.open('http://127.0.0.1:8000/api/techdraw/dxf?entity_id=part_001_techdraw', '_blank');
      });
    }

    const btnExportMonoArchive = document.getElementById('btn-export-mono-archive');
    if (btnExportMonoArchive) {
      btnExportMonoArchive.addEventListener('click', () => {
        btnExportMonoArchive.textContent = '⏳ Empaquetage...';
        this.sendMessage({
          type: 'export_mono_archive',
          title: inputTechdrawTitle ? inputTechdrawTitle.value : 'Projet Mono',
          author: 'Brandon',
        });
      });
    }

    const btnImportMonoTrigger = document.getElementById('btn-import-mono-trigger');
    const fileImportMono = document.getElementById('file-import-mono') as HTMLInputElement | null;

    if (btnImportMonoTrigger && fileImportMono) {
      btnImportMonoTrigger.addEventListener('click', () => {
        fileImportMono.click();
      });

      fileImportMono.addEventListener('change', async () => {
        const file = fileImportMono.files?.[0];
        if (!file) return;

        btnImportMonoTrigger.textContent = '⏳ Restauration...';
        const reader = new FileReader();
        reader.onload = async () => {
          const res = reader.result as string;
          const base64 = res.split(',')[1] || res;
          try {
            const resp = await fetch('http://127.0.0.1:8000/api/archive/import', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ archive_base64: base64 }),
            });
            const data = (await resp.json()) as { status: string; title: string; entities_restored: number };
            alert(`Projet .mono importé avec succès !\n• Titre : ${data.title}\n• Entités restaurées : ${data.entities_restored}`);
            this.sendMessage({ type: 'get_scene' });
          } catch (err) {
            alert(`Erreur d'import de l'archive .mono : ${err}`);
          } finally {
            btnImportMonoTrigger.textContent = '📂 Ouvrir Projet (.mono)';
          }
        };
        reader.readAsDataURL(file);
      });
    }

    // Architecture & BIM (IFC4 & HLR 2D)
    const btnBimFloorplanPreview = document.getElementById('btn-bim-floorplan-preview');
    const inputBimElevation = document.getElementById('input-bim-elevation') as HTMLInputElement | null;
    const btnBimDownloadDxf = document.getElementById('btn-bim-download-dxf');
    const btnBimDownloadIfc = document.getElementById('btn-bim-download-ifc');

    if (btnBimFloorplanPreview) {
      btnBimFloorplanPreview.addEventListener('click', async () => {
        const elev = inputBimElevation ? parseFloat(inputBimElevation.value) || 1.2 : 1.2;
        btnBimFloorplanPreview.textContent = '⏳ Calcul HLR...';
        try {
          const resp = await fetch(`http://127.0.0.1:8000/api/bim/floorplan?elevation_z=${elev}`);
          if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}`);
          }
          const svgText = await resp.text();
          const modal = document.getElementById('techdraw-modal');
          const container = document.getElementById('techdraw-svg-container');
          if (modal && container) {
            container.innerHTML = svgText;
            modal.style.display = 'flex';
          }
        } catch (err) {
          alert(`Erreur génération coupe BIM : ${err}`);
        } finally {
          btnBimFloorplanPreview.textContent = '👁️ Coupe HLR 2D';
        }
      });
    }

    if (btnBimDownloadDxf) {
      btnBimDownloadDxf.addEventListener('click', () => {
        const elev = inputBimElevation ? parseFloat(inputBimElevation.value) || 1.2 : 1.2;
        window.open(`http://127.0.0.1:8000/api/bim/dxf?elevation_z=${elev}`, '_blank');
      });
    }

    if (btnBimDownloadIfc) {
      btnBimDownloadIfc.addEventListener('click', () => {
        window.open('http://127.0.0.1:8000/api/bim/ifc', '_blank');
      });
    }
  }

  private playTrajectoryLoop(): void {
    this.trajectoryPlaybackTimer = window.setInterval(() => {
      if (!this.isTrajectoryPlaying) return;
      this.currentTrajectoryFrame = (this.currentTrajectoryFrame + 1) % this.totalTrajectoryFrames;
      if (this.sliderTrajectory) {
        this.sliderTrajectory.value = this.currentTrajectoryFrame.toString();
      }
      this.sendMessage({
        type: 'step_biomolecule_dynamics',
        entity_id: 'protein_001',
        frame_index: this.currentTrajectoryFrame,
      });

      if (this.currentTrajectoryFrame === this.totalTrajectoryFrames - 1) {
        // Fin de la boucle
      }
    }, 50); // 20 FPS pour la lecture fluide de dynamique moléculaire
  }

  private runTextileLoop(): void {
    if (!this.isTextileRunning) return;

    this.sendMessage({
      type: 'step_textile',
      entity_id: 'textile_001',
      n_frames: 1,
    });

    // Fréquence d'actualisation synchronisée avec l'écran (60 FPS)
    this.textileAnimationId = requestAnimationFrame(() => {
      // Temporisation légère pour ne pas saturer le buffer WebSocket si forte charge
      setTimeout(() => this.runTextileLoop(), 16);
    });
  }

  private connectWebSocket(): void {
    const wsUrl = 'ws://127.0.0.1:8000/ws';
    this.socket = new WebSocket(wsUrl);
    this.socket.binaryType = 'arraybuffer';

    this.socket.onopen = () => {
      this.statusElement.classList.remove('disconnected');
      this.statusElement.classList.add('connected');
      this.statusElement.querySelector('.status-text')!.textContent = 'Connecté (ws://127.0.0.1:8000)';
    };

    this.socket.onclose = () => {
      this.statusElement.classList.remove('connected');
      this.statusElement.classList.add('disconnected');
      this.statusElement.querySelector('.status-text')!.textContent = 'Déconnecté';
      // Tentative de reconnexion automatique après 2 secondes
      setTimeout(() => this.connectWebSocket(), 2000);
    };

    this.socket.onmessage = (event: MessageEvent) => {
      if (typeof event.data === 'string') {
        try {
          const msg = JSON.parse(event.data);
          this.handleJsonMessage(msg);
        } catch (e) {
          console.error('Erreur de parsing JSON WebSocket:', e);
        }
      } else if (event.data instanceof ArrayBuffer && this.lastHeader) {
        this.handleBinaryMeshPayload(event.data, this.lastHeader);
        this.lastHeader = null;
      }
    };
  }

  private handleJsonMessage(msg: Record<string, unknown>): void {
    const msgType = msg.type as string;

    if (msgType === 'welcome') {
      const scene = msg.scene as SceneSummary;
      if (scene && scene.parameters) {
        this.renderParameters(scene.parameters);
      }
    } else if (msgType === 'binary_mesh_header') {
      this.lastHeader = msg as unknown as BinaryMeshHeader;
    } else if (msgType === 'execution_success') {
      const vol = msg.volume_m3 as number;
      this.statVolume.textContent = `${(vol * 1e6).toFixed(1)} mm³`;
      this.statManifold.textContent = msg.is_valid_l0 ? 'VALIDE (L0)' : 'DÉGÉNÉRÉ';
      this.statManifold.className = msg.is_valid_l0 ? 'stat-value highlight' : 'stat-value error';
    } else if (msgType === 'param_updated') {
      const pName = msg.name as string;
      const pVal = msg.value as number;
      const valLabel = document.getElementById(`val-${pName}`);
      if (valLabel) {
        valLabel.textContent = pVal.toFixed(1);
      }
    } else if (msgType === 'fea_completed') {
      const btnFea = document.getElementById('btn-fea-simulate');
      if (btnFea) {
        btnFea.textContent = '⚡ Simulation FEA (CalculiX)';
      }
      const sf = msg.safety_factor as number;
      const vm = msg.max_von_mises_mpa as number;
      alert(`Simulation FEA terminée !\nContrainte max Von Mises : ${vm.toFixed(1)} MPa\nCoefficient de sécurité Sf : ${sf.toFixed(2)} (${sf >= 1.5 ? 'CERTIFIÉ SÛR' : 'SURCHARGE DÉTECTÉE'})`);
    } else if (msgType === 'fea_failed') {
      const btnFea = document.getElementById('btn-fea-simulate');
      if (btnFea) {
        btnFea.textContent = '⚡ Simulation FEA (CalculiX)';
      }
      alert(`Échec simulation FEA : ${msg.error}`);
    } else if (msgType === 'biomolecule_loaded') {
      const eid = msg.entity_id as string;
      const chains = (msg.chains as string[]) || [];
      const seq = (msg.sequence as string) || '';
      const crypto = msg.crystallography as {
        spacegroup?: string;
        resolution_angstrom?: number;
        secondary_structure?: { helices_count: number; sheets_count: number };
        disulfide_bridges_count?: number;
      } | undefined;

      const btnLoad = document.getElementById('btn-load-protein');
      if (btnLoad) btnLoad.textContent = '🧬 Charger Protéine';

      const statSpacegroup = document.getElementById('stat-bio-spacegroup');
      if (statSpacegroup && crypto?.spacegroup) {
        statSpacegroup.textContent = crypto.spacegroup;
      }

      const statRes = document.getElementById('stat-bio-resolution');
      if (statRes && crypto?.resolution_angstrom !== undefined) {
        statRes.textContent = crypto.resolution_angstrom > 0 ? `${crypto.resolution_angstrom} Å` : 'N/A';
      }

      const statSec = document.getElementById('stat-bio-secondary');
      if (statSec && crypto?.secondary_structure) {
        statSec.textContent = `${crypto.secondary_structure.helices_count} hel / ${crypto.secondary_structure.sheets_count} sht (${crypto.disulfide_bridges_count ?? 0} S-S)`;
      }

      if (this.statManifold) {
        this.statManifold.textContent = `BIO (${chains.length} ch, ${seq.length} aa)`;
        this.statManifold.className = 'stat-value highlight';
      }

      if (this.activeView === 'molstar') {
        this.molstarViewer.loadFromUrl(`http://127.0.0.1:8000/api/biomolecule/bcif?entity_id=${eid}`, 'bcif');
      }
    } else if (msgType === 'biomolecule_simulated') {
      const btnOpenmm = document.getElementById('btn-openmm-sim');
      if (btnOpenmm) {
        btnOpenmm.textContent = '⚡ Dynamique OpenMM';
      }
      const res = msg.result as {
        success: boolean;
        method: string;
        frame_count: number;
        initial_energy_kj_mol: number;
        final_energy_kj_mol: number;
        mean_rmsd_angstrom: number;
      };
      if (res && res.success) {
        this.totalTrajectoryFrames = res.frame_count;
        if (this.sliderTrajectory) {
          this.sliderTrajectory.max = Math.max(0, res.frame_count - 1).toString();
          this.sliderTrajectory.value = '0';
        }
        if (this.statBioEnergy) {
          this.statBioEnergy.textContent = `${res.final_energy_kj_mol.toFixed(1)} kJ/mol`;
        }
        if (this.labelRmsd) {
          this.labelRmsd.textContent = `${res.mean_rmsd_angstrom.toFixed(2)} Å (moy)`;
        }
        console.log(`Simulation OpenMM réussie : méthode ${res.method}, ${res.frame_count} frames calculées.`);
      }
    } else if (msgType === 'biomolecule_stepped') {
      const fIdx = msg.frame_index as number;
      const rmsd = msg.rmsd_angstrom as number;
      const energy = msg.potential_energy_kj_mol as number;
      if (this.labelFrame) {
        this.labelFrame.textContent = `Frame ${fIdx}`;
      }
      if (this.labelRmsd) {
        this.labelRmsd.textContent = `${rmsd.toFixed(2)} Å`;
      }
      if (this.statBioEnergy) {
        this.statBioEnergy.textContent = `${energy.toFixed(1)} kJ/mol`;
      }
    } else if (msgType === 'cam_toolpath_generated') {
      const btnGenerate = document.getElementById('btn-generate-cam');
      if (btnGenerate) {
        btnGenerate.textContent = '⚙️ Générer FAO';
      }
      const timeS = msg.estimated_machining_time_seconds as number;
      const scallop = msg.scallop_height_um as number;
      const statTime = document.getElementById('stat-cam-time');
      const statScallop = document.getElementById('stat-cam-scallop');
      if (statTime) statTime.textContent = `${timeS.toFixed(1)} s`;
      if (statScallop) statScallop.textContent = `${scallop.toFixed(2)} µm`;
      console.log(`Trajectoire FAO générée : ${msg.total_points} points, temps estimé : ${timeS}s`);
    } else if (msgType === 'additive_part_sliced') {
      const btnSlice = document.getElementById('btn-slice-additive');
      if (btnSlice) {
        btnSlice.textContent = '🖨️ Trancher 3D';
      }
      const mass = msg.estimated_mass_grams as number;
      const statMass = document.getElementById('stat-slicer-mass');
      if (statMass) statMass.textContent = `${mass.toFixed(2)} g`;
      console.log(`Tranchage 3D terminé : ${msg.layer_count} couches, ${mass.toFixed(1)}g PLA`);
    } else if (msgType === 'techdraw_generated') {
      const btnGen = document.getElementById('btn-generate-techdraw');
      if (btnGen) btnGen.textContent = '📐 Générer Plan 2D';

      this.lastTechDrawSvg = (msg.svg_content as string) || '';
      const statViews = document.getElementById('stat-techdraw-views');
      if (statViews) statViews.textContent = `${msg.views_count} vues`;

      const statSegments = document.getElementById('stat-techdraw-segments');
      if (statSegments) {
        statSegments.textContent = `${msg.total_visible_segments} / ${msg.total_hidden_segments}`;
      }
      console.log(`Plan TechDraw généré : ${msg.views_count} vues, ${msg.total_visible_segments} traits continus, ${msg.total_hidden_segments} pointillés cachés`);
    } else if (msgType === 'mono_archive_exported') {
      const btnExp = document.getElementById('btn-export-mono-archive');
      if (btnExp) btnExp.textContent = '📦 Sauvegarder .mono';

      const sizeKb = ((msg.archive_size_bytes as number) / 1024).toFixed(1);
      const statSize = document.getElementById('stat-archive-size');
      if (statSize) statSize.textContent = `${sizeKb} Ko`;

      window.open('http://127.0.0.1:8000/api/archive/download', '_blank');
      console.log(`Archive .mono créée : ${sizeKb} Ko, SHA256: ${(msg.archive_sha256 as string).substring(0, 12)}...`);
    }
  }

  private handleBinaryMeshPayload(buffer: ArrayBuffer, header: BinaryMeshHeader): void {
    const vLen = header.vertex_count * 3;
    const iLen = header.triangle_count * 3;
    const fLen = header.triangle_count;

    let offset = 0;
    // 1. Sommets (Float32)
    const vertices = new Float32Array(buffer, offset, vLen);
    offset += vLen * 4;

    // 2. Normales (Float32)
    const normals = new Float32Array(buffer, offset, vLen);
    offset += vLen * 4;

    // 3. Indices de triangles (Uint32)
    const indices = new Uint32Array(buffer, offset, iLen);
    offset += iLen * 4;

    // 4. Identifiants de face (Uint32)
    const faceIds = new Uint32Array(buffer, offset, fLen);
    offset += fLen * 4;

    // 5. Champ scalaire FEA optionnel (Float32)
    let scalarField: Float32Array | undefined = undefined;
    if (buffer.byteLength >= offset + header.vertex_count * 4) {
      scalarField = new Float32Array(buffer, offset, header.vertex_count);
    }

    // Mise à jour immédiate du viewport 3D Three.js
    this.viewport.updateMesh({
      entityId: header.entity_id,
      vertices,
      normals,
      indices,
      faceIds,
      scalarField,
    });

    // Mise à jour des compteurs HUD
    this.statTriangles.textContent = header.triangle_count.toLocaleString();
    this.statVertices.textContent = header.vertex_count.toLocaleString();
  }

  private renderParameters(parameters: Record<string, ParameterDef>): void {
    this.parametersContainer.innerHTML = '';
    const keys = Object.keys(parameters);

    if (keys.length === 0) {
      this.parametersContainer.innerHTML = '<div class="empty-state">Aucun paramètre interactif.</div>';
      return;
    }

    keys.forEach((key) => {
      const p = parameters[key];
      const item = document.createElement('div');
      item.className = 'param-item';

      item.innerHTML = `
        <div class="param-header">
          <span class="param-name">${p.label || key}</span>
          <span class="param-val" id="val-${key}">${p.value.toFixed(1)} ${p.unit}</span>
        </div>
        <input 
          type="range" 
          class="param-slider" 
          id="slider-${key}" 
          min="${p.min}" 
          max="${p.max}" 
          step="0.5" 
          value="${p.value}" 
        />
      `;

      const slider = item.querySelector(`#slider-${key}`) as HTMLInputElement;
      slider.addEventListener('input', () => {
        const val = parseFloat(slider.value);
        const label = document.getElementById(`val-${key}`);
        if (label) label.textContent = `${val.toFixed(1)} ${p.unit}`;

        // Envoi temps réel au backend sans recharger le modèle LLM
        this.sendMessage({
          type: 'update_param',
          name: key,
          value: val,
        });
      });

      this.parametersContainer.appendChild(item);
    });
  }

  private updateToolbarActiveMode(mode: TransformMode): void {
    this.btnToolOrbit?.classList.toggle('active', mode === 'none');
    this.btnToolTranslate?.classList.toggle('active', mode === 'translate');
    this.btnToolRotate?.classList.toggle('active', mode === 'rotate');
    this.btnToolScale?.classList.toggle('active', mode === 'scale');
  }

  private renderSceneTree(entities: SceneEntityInfo[]): void {
    if (!this.sceneTreeList || !this.treeEntityCount) return;

    this.treeEntityCount.textContent = entities.length.toString();

    if (entities.length === 0) {
      this.sceneTreeList.innerHTML = '<div class="tree-empty">Aucune entité 3D dans la scène</div>';
      return;
    }

    this.sceneTreeList.innerHTML = '';

    for (const entity of entities) {
      const itemEl = document.createElement('div');
      itemEl.className = `tree-item ${entity.selected ? 'selected' : ''} ${!entity.visible ? 'hidden-entity' : ''}`;
      itemEl.dataset.entityId = entity.id;

      // Badge du type
      const badge = document.createElement('span');
      badge.className = `tree-item-badge badge-${entity.type}`;
      badge.textContent = entity.type.toUpperCase();

      // Conteneur d'informations
      const infoContainer = document.createElement('div');
      infoContainer.className = 'tree-item-info';
      infoContainer.title = `${entity.id} (${entity.triangleCount.toLocaleString()} tri) - Clic pour sélectionner, Double-clic pour centrer`;

      const name = document.createElement('span');
      name.className = 'tree-item-name';
      name.textContent = entity.id;

      const meta = document.createElement('span');
      meta.className = 'tree-item-meta';
      meta.textContent = `${entity.triangleCount} tri`;

      infoContainer.appendChild(badge);
      infoContainer.appendChild(name);
      infoContainer.appendChild(meta);

      infoContainer.addEventListener('click', () => {
        this.viewport.selectEntity(entity.id);
      });

      infoContainer.addEventListener('dblclick', () => {
        this.viewport.focusEntity(entity.id);
      });

      // Actions sur l'entité
      const actions = document.createElement('div');
      actions.className = 'tree-item-actions';

      // Bouton Visibilité
      const btnEye = document.createElement('button');
      btnEye.className = 'btn-icon';
      btnEye.title = entity.visible ? 'Masquer (H)' : 'Afficher (H)';
      btnEye.textContent = entity.visible ? '👁️' : '🕶️';
      btnEye.addEventListener('click', (e) => {
        e.stopPropagation();
        this.viewport.toggleEntityVisibility(entity.id);
      });

      // Bouton Centrer la vue
      const btnFocus = document.createElement('button');
      btnFocus.className = 'btn-icon';
      btnFocus.title = 'Centrer la caméra (F)';
      btnFocus.textContent = '🎯';
      btnFocus.addEventListener('click', (e) => {
        e.stopPropagation();
        this.viewport.focusEntity(entity.id);
      });

      // Bouton Supprimer
      const btnDel = document.createElement('button');
      btnDel.className = 'btn-icon danger';
      btnDel.title = 'Supprimer l\'entité';
      btnDel.textContent = '🗑️';
      btnDel.addEventListener('click', (e) => {
        e.stopPropagation();
        this.viewport.removeEntity(entity.id);
      });

      actions.appendChild(btnEye);
      actions.appendChild(btnFocus);
      actions.appendChild(btnDel);

      itemEl.appendChild(infoContainer);
      itemEl.appendChild(actions);

      this.sceneTreeList.appendChild(itemEl);
    }
  }

  private updateTreeSelection(selectedId: string | null): void {
    if (!this.sceneTreeList) return;
    const items = this.sceneTreeList.querySelectorAll('.tree-item');
    items.forEach((el) => {
      const htmlEl = el as HTMLElement;
      if (htmlEl.dataset.entityId === selectedId) {
        htmlEl.classList.add('selected');
      } else {
        htmlEl.classList.remove('selected');
      }
    });
  }

  private sendMessage(msg: Record<string, unknown>): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(msg));
    }
  }
}

// Initialisation dès que le DOM est prêt
window.addEventListener('DOMContentLoaded', () => {
  new MonoApp();
});
