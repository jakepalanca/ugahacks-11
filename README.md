# Magic Minecraft Builder

## Team Members
- **Jake Palanca**
- **Andrew Cao**
- **Jared Sonkesak**
- **Pavan Gadiraju**

---

## Purpose
**Magic Minecraft Builder** turns **text into structures inside Minecraft**.

Instead of placing blocks manually, a player joins our server, runs a command to receive a **magical wand**, clicks the ground, types a prompt like *"a giant bulldog with a top hat,"* chooses a scale, and the system generates a **voxelized 3D model** and builds it **next to the player** in-game.

---

## What It Does

### User Experience (In-Game)
1. Player joins the Minecraft server
2. Player runs '/createbuild'
3. Player receives a **wand**
4. Player clicks the ground to set a build location
5. Player enters:
   - a **text prompt**
   - a **scale**
6. Within moments, the AI-generated object is **constructed in blocks** beside the player

### System Behavior (Behind the Scenes)
- The server sends the prompt to our AWS backend
- The backend generates an image, converts it into a 3D model, voxelizes it into block coordinates, then sends coordinates back
- The server places blocks to render the structure

---

## How We Built It (Pipeline)
We built a multi-stage pipeline leveraging AWS. Here is the flow from text to blocks:

1. **Text-to-Image**
   - The server receives the user prompt and sends it to an AWS backend.
   - We use **Amazon Titan Image Generator** to create a high-quality isometric view of the requested object.
   - We automatically remove the background from the generated image to ensure a clean model.

2. **Image-to-3D**
   - We feed the clean image into **HunYuan3D 2.1**, which converts the 2D reference into a textured 3D mesh.

3. **Voxelization**
   - We convert the 3D mesh into Minecraft block coordinates (voxel grid).

4. **Rendering**
   - The coordinates are returned to the server.
   - The server places blocks in the world to render the generated structure next to the player.

---

## Tools Utilized

### Minecraft / Game Side
- **Minecraft Java Server** (Vanilla / Paper / Spigot — *specify which you used*)
- **Java Plugin / Texture Pack Integration** (implements '/createbuild', wand interaction, and block placement — *specify your exact server/plugin stack*)

### Cloud / Backend
- **AWS Lambda** (initial attempt; limited by dependency size/runtime)
- **AWS SageMaker** (final compute solution for heavy AI dependencies)
- **AWS S3** (optional storage for images/meshes/intermediate artifacts)
- **AWS IAM** (permissions and secure service-to-service access)

### AI / Modeling Pipeline
- **Amazon Titan Image Generator** (text-to-image)
- **Background Removal** (clean subject isolation)
- **HunYuan3D 2.1** (image-to-3D mesh + texture)
- **Voxelization Module** (mesh → Minecraft block coordinates)

### Development
- **Python** (pipeline + orchestration)
- **Docker** (containerization for SageMaker deployment)
- **Git/GitHub** (version control + collaboration)

---

## Public Frameworks / APIs Credits
We used and integrated the following public tools/frameworks/APIs:

- **Amazon Titan Image Generator** (AWS Bedrock model)
- **HunYuan3D 2.1** (image-to-3D generation)
- **AWS SageMaker, Lambda, S3, IAM** (cloud infrastructure and deployment)
- **Minecraft Java Edition server API / modding framework** (*Paper/Spigot API or NeoForge/Forge/Fabric — fill in what you used*)

---

## Challenges We Ran Into (and How We Overcame Them)

### Dependencys
**Problem:** Our prototype depended on many heavy Python modules. When we tried moving to AWS Lambda, the runtime didn’t support our dependencies.  
**Solution:** We pivoted to **SageMaker + Docker**, which gave us full control over system libraries and Python packages.

### Latency & Timeouts
**Problem:** 3D generation is computationally expensive and initially took too long for a smooth Minecraft experience.  
**Solution:** We optimized the pipeline flow and moved compute to scalable AWS infrastructure (SageMaker), reducing timeouts and improving reliability.

### File Size Management
**Problem:** We attempted to bundle a virtual environment to fix dependencies, but the deployment exceeded AWS Lambda size limits.  
**Solution:** Containerization (Docker) on SageMaker removed strict package size constraints and stabilized our deployment.

### Token Management
**Problem:** Frequent testing consumed API tokens quickly.  
**Solution:** We reduced unnecessary regenerations, controlled retries, and monitored usage throughout development.

---

## Accomplishments That We're Proud Of

### SageMaker
Successfully configuring a **SageMaker Docker environment** that could run our AI pipeline reliably was our biggest technical win.

### The “First Block” Moment
Seeing the very first AI-generated structure appear in the Minecraft world proved the full pipeline worked end-to-end.

---

## What We Learned
- **Cloud Infrastructure:** Managing on-demand compute and deployment on AWS
- **Pipeline Orchestration:** Chaining distinct AI models into one functional system (Titan → HunYuan → voxelizer)
- **Systems Integration:** Connecting a Minecraft server workflow to a cloud AI backend

---

## What's Next
- **The Spellbook:** Save generated models so users can respawn them later without regenerating
- **Scaling Spells:** Add command arguments to control object size/scale
- **Material Matching:** Improve block palette mapping to better match colors/textures
- **Speedups:** Optimize voxelization and server-side placement for large builds

---

## How to Run
1. Open 'Minecraft (Java Edition)'.
2. Deploy the stack (`infra/cloudformation/scripts/deploy.sh`) and use the `MinecraftPublicIp` output (Elastic IP when `ALLOCATE_ELASTIC_IP=true`).
3. Join `<MinecraftPublicIp>:25565` from your Minecraft client.
4. Once in-game, run the command: '/createbuild'
5. A 'wand' will appear in your inventory—equip it and 'right-click a block' to select the destination where the model should be built.
6. In chat, type the 'prompt' for what you want to generate (e.g., '"a giant bulldog with a top hat"') and press 'Enter'.
7. In chat, type the desired 'size': 'small', 'medium', or 'large', then press 'Enter'.
8. Wait a few minutes—your structure will generate and load into the world at the selected location.

Resolve the current Minecraft endpoint (Elastic IP) from CloudFormation:
```bash
aws cloudformation describe-stacks \
  --stack-name <stack-name> \
  --region <aws-region> \
  --query "Stacks[0].Outputs[?OutputKey=='MinecraftPublicIp'].OutputValue | [0]" \
  --output text
```

## Infrastructure Deployment 
- Full-stack AWS deployment (backend + Minecraft server + teardown):
  - `infra/cloudformation/README.md`
- Minecraft AI Builder (SageMaker runtime) details:
  - `sagemaker_runtime/README.md`
- Detailed pipeline/ops notes:
  - `docs/ABOUT.md`
- Per-service docs (Lambdas, SageMaker, API, IAM, EC2, S3, DynamoDB):
  - `docs/services/README.md`
- Deploy:
  - `cd infra/cloudformation`
  - `./scripts/deploy.sh`
- Destroy everything:
  - `cd infra/cloudformation`
  - `./scripts/destroy.sh`

- Repository structure map:
  - `docs/REPO_STRUCTURE.md`

---
