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

  constructor() {
    const canvas = document.getElementById('cad-canvas') as HTMLCanvasElement;
    this.viewport = new CadViewport(canvas);

    this.parametersContainer = document.getElementById('parameters-container')!;
    this.statusElement = document.getElementById('connection-status')!;
    this.statManifold = document.getElementById('stat-manifold')!;
    this.statVolume = document.getElementById('stat-volume')!;
    this.statTriangles = document.getElementById('stat-triangles')!;
    this.statVertices = document.getElementById('stat-vertices')!;

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

    // Mise à jour immédiate du viewport 3D Three.js
    this.viewport.updateMesh({
      entityId: header.entity_id,
      vertices,
      normals,
      indices,
      faceIds,
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
