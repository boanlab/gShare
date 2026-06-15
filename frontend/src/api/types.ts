// Domain type aliases: friendlier names layered over the generated schema.d.ts components.schemas.
import type { components } from './schema';

type Schemas = components['schemas'];

export type Session = Schemas['SessionRead'];
export type Volume = Schemas['VolumeRead'];
export type CreateSessionBody = Schemas['SessionCreate'];

// resource_class is a string constrained by the backend to pattern="^(gpu|cpu)$".
export type ResourceClass = 'gpu' | 'cpu';
