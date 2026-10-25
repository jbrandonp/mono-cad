import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { TransformControls } from 'three/examples/jsm/controls/TransformControls.js';

export type EntityType = 'brep' | 'bim' | 'fea' | 'cam' | 'textile' | 'bio' | 'generic';
export type TransformMode = 'translate' | 'rotate' | 'scale' | 'none';

export interface BinaryMeshUpdate {
  entityId: string;
  vertices: Float32Array;
  normals: Float32Array;
  indices: Uint32Array;
  faceIds: Uint32Array;
  scalarField?: Float32Array;
}

export interface SceneEntityInfo {
  id: string;
  type: EntityType;
  visible: boolean;
  triangleCount: number;
  vertexCount: number;
  selected: boolean;
}

interface InternalSceneEntity {
  id: string;
  type: EntityType;
  group: THREE.Group;
  mesh: THREE.Mesh;
  edges: THREE.LineSegments;
  visible: boolean;
  triangleCount: number;
  vertexCount: number;
}

export class CadViewport {
  private canvas: HTMLCanvasElement;
  private renderer: THREE.WebGLRenderer;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;
  private controls: OrbitControls;
  private transformControls: TransformControls;
  private activeMeshes: Map<string, InternalSceneEntity> = new Map();
  private selectedEntityId: string | null = null;
  private currentTransformMode: TransformMode = 'translate';
  private isTransformDragging: boolean = false;
  private raycaster: THREE.Raycaster;
  private mouse: THREE.Vector2;
  private pointerDownPos: THREE.Vector2;
  private shadowGround: THREE.Mesh;

  public onEntitiesChanged?: (entities: SceneEntityInfo[]) => void;
  public onEntitySelected?: (entityId: string | null) => void;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;

    // 1. Initialisation WebGL avec tampon logarithmique anti Z-fighting et Tone Mapping ACES
    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
      logarithmicDepthBuffer: true,
      powerPreference: 'high-performance',
    });
    this.renderer.setSize(this.canvas.clientWidth, this.canvas.clientHeight);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x0f172a, 1.0);

    // Ombres douces PBR
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;

    // 2. Scène et Caméra Perspective RTC
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(
      45,
      this.canvas.clientWidth / this.canvas.clientHeight,
      0.001,
      1500.0
    );
    this.camera.position.set(130, 110, 150);

    // 3. Contrôleur d'orbite fluide
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.05;
    this.controls.target.set(0, 0, 0);

    // 4. Gizmo de Transformation 3D (TransformControls)
    this.transformControls = new TransformControls(this.camera, this.renderer.domElement);
    this.transformControls.size = 0.85;
    this.transformControls.addEventListener('dragging-changed', (event: { value: unknown }) => {
      this.isTransformDragging = Boolean(event.value);
      this.controls.enabled = !event.value;
    });
    this.scene.add(this.transformControls.getHelper());

    // 5. Pipeline d'Éclairage Studio PBR (Key + Fill + Rim + Hemisphere)
    const ambientLight = new THREE.HemisphereLight(0xf1f5f9, 0x1e293b, 0.65);
    this.scene.add(ambientLight);

    // Key Light directionnelle principale avec ombres douces
    const keyLight = new THREE.DirectionalLight(0xffffff, 1.7);
    keyLight.position.set(160, 240, 140);
    keyLight.castShadow = true;
    keyLight.shadow.mapSize.width = 2048;
    keyLight.shadow.mapSize.height = 2048;
    keyLight.shadow.camera.near = 0.5;
    keyLight.shadow.camera.far = 1000;
    keyLight.shadow.camera.left = -220;
    keyLight.shadow.camera.right = 220;
    keyLight.shadow.camera.top = 220;
    keyLight.shadow.camera.bottom = -220;
    keyLight.shadow.bias = -0.0001;
    keyLight.shadow.radius = 2.5;
    this.scene.add(keyLight);

    // Fill Light douce pour déboucher les ombres mécaniques
    const fillLight = new THREE.DirectionalLight(0xa5c4e8, 0.75);
    fillLight.position.set(-160, 80, -100);
    this.scene.add(fillLight);

    // Rim Light cyan pour détacher les arêtes des pièces
    const rimLight = new THREE.DirectionalLight(0x38bdf8, 0.8);
    rimLight.position.set(0, -180, -140);
    this.scene.add(rimLight);

    // 6. Grille d'ingénierie et plan récepteur d'ombres douces
    const grid = new THREE.GridHelper(300, 30, 0x00d2ff, 0x334155);
    grid.position.y = 0;
    this.scene.add(grid);

    const shadowGroundGeo = new THREE.PlaneGeometry(800, 800);
    const shadowGroundMat = new THREE.ShadowMaterial({ opacity: 0.35 });
    this.shadowGround = new THREE.Mesh(shadowGroundGeo, shadowGroundMat);
    this.shadowGround.rotation.x = -Math.PI / 2;
    this.shadowGround.position.y = -0.05;
    this.shadowGround.receiveShadow = true;
    this.scene.add(this.shadowGround);

    // 7. Raycasting et gestion de sélection
    this.raycaster = new THREE.Raycaster();
    this.mouse = new THREE.Vector2();
    this.pointerDownPos = new THREE.Vector2();

    this.setupInteractions();

    // Gestion du redimensionnement dynamique de fenêtre
    window.addEventListener('resize', () => this.onResize());

    // Démarrage de la boucle de rendu à 60 FPS
    this.animate();
  }

  private setupInteractions(): void {
    this.canvas.addEventListener('pointerdown', (e: PointerEvent) => {
      this.pointerDownPos.set(e.clientX, e.clientY);
    });

    this.canvas.addEventListener('pointerup', (e: PointerEvent) => {
      const dist = Math.hypot(e.clientX - this.pointerDownPos.x, e.clientY - this.pointerDownPos.y);
      if (dist > 5) return; // Mouvement de caméra ignoré
      if (this.isTransformDragging) return;

      const rect = this.canvas.getBoundingClientRect();
      this.mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      this.mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

      this.raycaster.setFromCamera(this.mouse, this.camera);
      const meshes: THREE.Mesh[] = [];
      const idMap = new Map<THREE.Mesh, string>();

      for (const [id, entity] of this.activeMeshes.entries()) {
        if (entity.visible) {
          meshes.push(entity.mesh);
          idMap.set(entity.mesh, id);
        }
      }

      const intersects = this.raycaster.intersectObjects(meshes, false);
      if (intersects.length > 0) {
        const hitMesh = intersects[0].object as THREE.Mesh;
        const hitId = idMap.get(hitMesh);
        if (hitId) {
          this.selectEntity(hitId);
        }
      } else {
        this.selectEntity(null);
      }
    });

    window.addEventListener('keydown', (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT')) {
        return;
      }

      switch (e.key.toLowerCase()) {
        case 't':
          this.setTransformMode('translate');
          break;
        case 'r':
          this.setTransformMode('rotate');
          break;
        case 's':
          this.setTransformMode('scale');
          break;
        case 'escape':
          this.setTransformMode('none');
          this.selectEntity(null);
          break;
        case 'f':
          if (this.selectedEntityId) {
            this.focusEntity(this.selectedEntityId);
          }
          break;
        case 'h':
          if (this.selectedEntityId) {
            this.toggleEntityVisibility(this.selectedEntityId);
          }
          break;
      }
    });
  }

  public updateMesh(data: BinaryMeshUpdate): void {
    if (this.activeMeshes.has(data.entityId)) {
      this.removeEntity(data.entityId, false);
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(data.vertices, 3));
    geometry.setAttribute('normal', new THREE.BufferAttribute(data.normals, 3));
    geometry.setIndex(new THREE.BufferAttribute(data.indices, 1));
    geometry.computeBoundingBox();

    const type = this.detectEntityType(data.entityId, data.scalarField);

    let material: THREE.MeshStandardMaterial;

    if (data.scalarField && data.scalarField.length > 0) {
      // Heatmap FEA Von Mises
      const colors = new Float32Array(data.scalarField.length * 3);
      for (let i = 0; i < data.scalarField.length; i++) {
        const val = Math.max(0, Math.min(1, data.scalarField[i]));
        let r = 0, g = 0, b = 0;
        if (val < 0.25) {
          r = 0; g = val * 4; b = 1;
        } else if (val < 0.5) {
          r = 0; g = 1; b = 1 - (val - 0.25) * 4;
        } else if (val < 0.75) {
          r = (val - 0.5) * 4; g = 1; b = 0;
        } else {
          r = 1; g = 1 - (val - 0.75) * 4; b = 0;
        }
        colors[i * 3] = r;
        colors[i * 3 + 1] = g;
        colors[i * 3 + 2] = b;
      }
      geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
      material = new THREE.MeshStandardMaterial({
        vertexColors: true,
        metalness: 0.15,
        roughness: 0.40,
        side: THREE.DoubleSide,
      });
    } else {
      switch (type) {
        case 'bim':
          material = new THREE.MeshStandardMaterial({
            color: 0xcbd5e1,
            metalness: 0.15,
            roughness: 0.70,
            side: THREE.DoubleSide,
          });
          break;
        case 'textile':
          material = new THREE.MeshStandardMaterial({
            color: 0x38bdf8,
            metalness: 0.05,
            roughness: 0.85,
            side: THREE.DoubleSide,
          });
          break;
        case 'cam':
          material = new THREE.MeshStandardMaterial({
            color: 0xf59e0b,
            metalness: 0.45,
            roughness: 0.35,
            side: THREE.DoubleSide,
          });
          break;
        default: // brep mécanique
          material = new THREE.MeshStandardMaterial({
            color: 0x94a3b8,
            metalness: 0.65,
            roughness: 0.28,
            side: THREE.DoubleSide,
          });
          break;
      }
    }

    const mesh = new THREE.Mesh(geometry, material);
    mesh.castShadow = true;
    mesh.receiveShadow = true;

    // Arêtes vives CAO
    const edgesGeom = new THREE.EdgesGeometry(geometry, 25);
    const edgesMat = new THREE.LineBasicMaterial({
      color: type === 'textile' ? 0x0284c7 : 0x0f172a,
      linewidth: 1.5,
    });
    const edges = new THREE.LineSegments(edgesGeom, edgesMat);

    // Groupe racine de l'entité
    const group = new THREE.Group();
    group.name = data.entityId;
    group.add(mesh);
    group.add(edges);
    this.scene.add(group);

    const triangleCount = data.indices.length / 3;
    const vertexCount = data.vertices.length / 3;

    this.activeMeshes.set(data.entityId, {
      id: data.entityId,
      type,
      group,
      mesh,
      edges,
      visible: true,
      triangleCount,
      vertexCount,
    });

    if (this.activeMeshes.size === 1 && geometry.boundingBox) {
      const center = new THREE.Vector3();
      geometry.boundingBox.getCenter(center);
      this.controls.target.copy(center);
    }

    this.notifyEntitiesChanged();
  }

  public removeEntity(entityId: string, notify: boolean = true): void {
    if (!this.activeMeshes.has(entityId)) return;
    const entity = this.activeMeshes.get(entityId)!;

    if (this.selectedEntityId === entityId) {
      this.selectEntity(null);
    }

    this.scene.remove(entity.group);
    entity.mesh.geometry.dispose();
    if (Array.isArray(entity.mesh.material)) {
      entity.mesh.material.forEach((m) => m.dispose());
    } else {
      entity.mesh.material.dispose();
    }
    entity.edges.geometry.dispose();
    if (Array.isArray(entity.edges.material)) {
      entity.edges.material.forEach((m) => m.dispose());
    } else {
      entity.edges.material.dispose();
    }

    this.activeMeshes.delete(entityId);

    if (notify) {
      this.notifyEntitiesChanged();
    }
  }

  public clearScene(): void {
    const ids = Array.from(this.activeMeshes.keys());
    for (const id of ids) {
      this.removeEntity(id, false);
    }
    this.selectEntity(null);
    this.notifyEntitiesChanged();
  }

  public setEntityVisibility(entityId: string, visible: boolean): void {
    if (!this.activeMeshes.has(entityId)) return;
    const entity = this.activeMeshes.get(entityId)!;
    entity.visible = visible;
    entity.group.visible = visible;

    if (!visible && this.selectedEntityId === entityId) {
      this.transformControls.detach();
    }
    this.notifyEntitiesChanged();
  }

  public toggleEntityVisibility(entityId: string): boolean {
    if (!this.activeMeshes.has(entityId)) return false;
    const entity = this.activeMeshes.get(entityId)!;
    this.setEntityVisibility(entityId, !entity.visible);
    return entity.visible;
  }

  public focusEntity(entityId: string): void {
    if (!this.activeMeshes.has(entityId)) return;
    const entity = this.activeMeshes.get(entityId)!;
    const box = new THREE.Box3().setFromObject(entity.group);
    const center = new THREE.Vector3();
    const size = new THREE.Vector3();
    box.getCenter(center);
    box.getSize(size);

    const maxDim = Math.max(size.x, size.y, size.z, 20);
    this.controls.target.copy(center);
    this.camera.position.set(center.x + maxDim * 1.5, center.y + maxDim * 1.3, center.z + maxDim * 1.7);
    this.controls.update();
  }

  public selectEntity(entityId: string | null): void {
    this.selectedEntityId = entityId;

    if (!entityId || !this.activeMeshes.has(entityId)) {
      this.transformControls.detach();
      this.selectedEntityId = null;
    } else {
      const entity = this.activeMeshes.get(entityId)!;
      if (this.currentTransformMode !== 'none' && entity.visible) {
        this.transformControls.attach(entity.group);
      } else {
        this.transformControls.detach();
      }
    }

    this.onEntitySelected?.(this.selectedEntityId);
    this.notifyEntitiesChanged();
  }

  public getSelectedEntityId(): string | null {
    return this.selectedEntityId;
  }

  public setTransformMode(mode: TransformMode): void {
    this.currentTransformMode = mode;
    if (mode === 'none') {
      this.transformControls.detach();
    } else {
      this.transformControls.setMode(mode);
      if (this.selectedEntityId && this.activeMeshes.has(this.selectedEntityId)) {
        const entity = this.activeMeshes.get(this.selectedEntityId)!;
        if (entity.visible) {
          this.transformControls.attach(entity.group);
        }
      }
    }
  }

  public getTransformMode(): TransformMode {
    return this.currentTransformMode;
  }

  public toggleShadows(enabled?: boolean): boolean {
    const nextState = enabled !== undefined ? enabled : !this.renderer.shadowMap.enabled;
    this.renderer.shadowMap.enabled = nextState;
    this.shadowGround.visible = nextState;
    this.scene.traverse((obj) => {
      if (obj instanceof THREE.Mesh && obj !== this.shadowGround) {
        obj.castShadow = nextState;
        obj.receiveShadow = nextState;
        if (obj.material) {
          const mat = obj.material as THREE.Material;
          mat.needsUpdate = true;
        }
      }
    });
    return nextState;
  }

  public getEntityList(): SceneEntityInfo[] {
    const list: SceneEntityInfo[] = [];
    for (const [id, item] of this.activeMeshes.entries()) {
      list.push({
        id,
        type: item.type,
        visible: item.visible,
        triangleCount: item.triangleCount,
        vertexCount: item.vertexCount,
        selected: this.selectedEntityId === id,
      });
    }
    return list;
  }

  private notifyEntitiesChanged(): void {
    if (this.onEntitiesChanged) {
      this.onEntitiesChanged(this.getEntityList());
    }
  }

  private detectEntityType(
    id: string,
    scalarField?: Float32Array
  ): EntityType {
    const lower = id.toLowerCase();
    if (scalarField && scalarField.length > 0) return 'fea';
    if (lower.startsWith('bim_') || lower.startsWith('ifc_')) return 'bim';
    if (lower.startsWith('fea_') || lower.startsWith('stress_')) return 'fea';
    if (lower.startsWith('cam_') || lower.startsWith('toolpath_') || lower.startsWith('gcode_')) return 'cam';
    if (lower.startsWith('textile_') || lower.startsWith('cloth_') || lower.startsWith('drape_')) return 'textile';
    if (lower.startsWith('bio_') || lower.startsWith('protein_') || lower.startsWith('pdb_')) return 'bio';
    if (lower.startsWith('part_') || lower.startsWith('brep_') || lower.startsWith('solid_')) return 'brep';
    return 'generic';
  }

  private onResize(): void {
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    if (w === 0 || h === 0) return;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
  }

  private animate = (): void => {
    requestAnimationFrame(this.animate);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  };
}
