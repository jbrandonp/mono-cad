import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

export interface BinaryMeshUpdate {
  entityId: string;
  vertices: Float32Array;
  normals: Float32Array;
  indices: Uint32Array;
  faceIds: Uint32Array;
}

export class CadViewport {
  private canvas: HTMLCanvasElement;
  private renderer: THREE.WebGLRenderer;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;
  private controls: OrbitControls;
  private activeMeshes: Map<string, { mesh: THREE.Mesh; edges: THREE.LineSegments }> = new Map();

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;

    // 1. Initialisation WebGL avec tampon logarithmique anti Z-fighting
    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
      logarithmicDepthBuffer: true,
      powerPreference: 'high-performance',
    });
    this.renderer.setSize(this.canvas.clientWidth, this.canvas.clientHeight);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x12151c, 1.0);

    // 2. Scène et Caméra Perspective RTC
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(
      45,
      this.canvas.clientWidth / this.canvas.clientHeight,
      0.001,
      1000.0
    );
    this.camera.position.set(120, 100, 140);

    // 3. Contrôleur d'orbite fluide
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.05;
    this.controls.target.set(0, 0, 0);

    // 4. Éclairage d'ingénierie CAO (Key + Fill + Ambient)
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    this.scene.add(ambientLight);

    const dirLight1 = new THREE.DirectionalLight(0xffffff, 1.2);
    dirLight1.position.set(150, 200, 100);
    this.scene.add(dirLight1);

    const dirLight2 = new THREE.DirectionalLight(0x88aaff, 0.5);
    dirLight2.position.set(-150, -100, -100);
    this.scene.add(dirLight2);

    // 5. Grille d'ingénierie sur le plan Z=0
    const grid = new THREE.GridHelper(300, 30, 0x00d2ff, 0x2e3646);
    grid.rotation.x = Math.PI / 2; // Plan XY ou XZ selon orientation
    this.scene.add(grid);

    // Gestion du redimensionnement dynamique de fenêtre
    window.addEventListener('resize', () => this.onResize());

    // Démarrage de la boucle de rendu à 60 FPS
    this.animate();
  }

  public updateMesh(data: BinaryMeshUpdate): void {
    // Si un maillage existe déjà pour cette entité, le retirer proprement
    if (this.activeMeshes.has(data.entityId)) {
      const existing = this.activeMeshes.get(data.entityId)!;
      this.scene.remove(existing.mesh);
      this.scene.remove(existing.edges);
      existing.mesh.geometry.dispose();
      existing.edges.geometry.dispose();
      this.activeMeshes.delete(data.entityId);
    }

    // 1. Instanciation du BufferGeometry Three.js directement depuis les tampons binaires (Zero-Copy)
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(data.vertices, 3));
    geometry.setAttribute('normal', new THREE.BufferAttribute(data.normals, 3));
    geometry.setIndex(new THREE.BufferAttribute(data.indices, 1));
    geometry.computeBoundingBox();

    // 2. Matériau PBR mécanique métallique doux (Aluminium 6061)
    const material = new THREE.MeshStandardMaterial({
      color: 0x64748b,
      metalness: 0.6,
      roughness: 0.35,
      side: THREE.DoubleSide,
    });

    const mesh = new THREE.Mesh(geometry, material);
    this.scene.add(mesh);

    // 3. Arêtes vives CAO (Edge Wireframe noir/cyan)
    const edgesGeom = new THREE.EdgesGeometry(geometry, 25); // Seuil angulaire 25°
    const edgesMat = new THREE.LineBasicMaterial({ color: 0x0f172a, linewidth: 1.5 });
    const edges = new THREE.LineSegments(edgesGeom, edgesMat);
    this.scene.add(edges);

    this.activeMeshes.set(data.entityId, { mesh, edges });

    // Cadrage automatique de la caméra sur le solide
    if (geometry.boundingBox) {
      const center = new THREE.Vector3();
      geometry.boundingBox.getCenter(center);
      this.controls.target.copy(center);
    }
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
