// Domain type aliases: friendlier names layered over the generated schema.d.ts components.schemas.
import type { components } from './schema';

type Schemas = components['schemas'];

// disk_used_bytes / disk_limit_bytes are served by GET /sessions/{id} but are not in the
// generated schema.d.ts yet (regenerated separately); typed here until then. Both are a
// ~5-minute-stale kubelet reading, present only while the platform has a recent sample.
export type Session = Schemas['SessionRead'] & {
  disk_used_bytes?: number | null;
  disk_limit_bytes?: number | null;
};
export type Volume = Schemas['VolumeRead'];
export type CreateSessionBody = Schemas['SessionCreate'];

// resource_class is a string constrained by the backend to pattern="^(gpu|cpu)$".
export type ResourceClass = 'gpu' | 'cpu';
