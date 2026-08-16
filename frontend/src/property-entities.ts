import type { Entity } from './components/EntityOverlay'

export function entitiesOwnedByProperty(propertyId: string, entities: Entity[]): Entity[] {
  return entities.filter((entity) => entity.source_property_ids?.includes(propertyId))
}
