import { CadViewport } from './viewport.ts';

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

    this.setupUIHandlers();
    this.connectWebSocket();
  }

  private setupUIHandlers(): void {
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
