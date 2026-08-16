type Capability = { id: string; label: string }

const groups: Array<{ label: string; capabilities: Capability[] }> = [
  { label: 'Projects', capabilities: [{ id: 'project.view', label: 'View projects' }, { id: 'project.create', label: 'Create projects' }, { id: 'project.rename', label: 'Rename projects' }, { id: 'project.delete', label: 'Delete projects' }] },
  { label: 'Properties', capabilities: [{ id: 'property.view', label: 'View properties' }, { id: 'property.upload', label: 'Add properties' }, { id: 'property.replace', label: 'Replace property files' }, { id: 'property.edit', label: 'Edit properties' }, { id: 'property.delete', label: 'Delete properties' }, { id: 'property.rename', label: 'Rename properties' }, { id: 'property.move', label: 'Re-group properties' }, { id: 'property.attribute.view', label: 'View property attributes' }, { id: 'property.attribute.edit', label: 'Edit property attributes' }] },
  { label: 'Graphs and answers', capabilities: [{ id: 'graph.property.view', label: 'View Property Graph' }, { id: 'graph.entity.view', label: 'View Entity Graph' }, { id: 'search.properties', label: 'Search properties' }, { id: 'search.entities', label: 'Search entities' }, { id: 'query.execute', label: 'Run AI Query' }] },
  { label: 'Processing', capabilities: [{ id: 'agent.status.view', label: 'View processing status' }, { id: 'agent.retry', label: 'Retry processing' }, { id: 'agent.cancel', label: 'Cancel processing' }] },
  { label: 'Administration', capabilities: [{ id: 'system.config.view', label: 'View system configuration' }, { id: 'system.config.edit', label: 'Edit system configuration' }, { id: 'user.manage', label: 'Manage users' }, { id: 'group.manage', label: 'Manage groups' }, { id: 'role.manage', label: 'Manage roles' }] },
  { label: 'MCP', capabilities: [{ id: 'mcp.use', label: 'Use project MCP endpoints' }, { id: 'mcp.configure', label: 'Configure project MCP endpoints' }] },
]

export default function CapabilityPicker({ value, onChange }: { value: string[]; onChange: (capabilities: string[]) => void }) {
  const selected = new Set(value)
  function toggle(capability: string) {
    const next = new Set(selected)
    if (next.has(capability)) next.delete(capability)
    else next.add(capability)
    onChange([...next].sort())
  }
  function toggleGroup(capabilities: Capability[]) {
    const next = new Set(selected)
    const allSelected = capabilities.every(capability => next.has(capability.id))
    capabilities.forEach(capability => allSelected ? next.delete(capability.id) : next.add(capability.id))
    onChange([...next].sort())
  }

  return <div className="capability-picker" aria-label="Role capabilities">
    <div className="capability-picker-summary"><span>Capabilities</span><strong>{value.length} selected</strong></div>
    {groups.map(group => <fieldset className="capability-group" key={group.label}><legend>{group.label}<button type="button" aria-label={`${group.capabilities.every(capability => selected.has(capability.id)) ? 'Clear' : 'Select all'} ${group.label} capabilities`} onClick={() => toggleGroup(group.capabilities)}>{group.capabilities.every(capability => selected.has(capability.id)) ? 'Clear' : 'Select all'}</button></legend><div className="capability-options">{group.capabilities.map(capability => <label className="capability-option" key={capability.id}><input type="checkbox" aria-label={capability.label} checked={selected.has(capability.id)} onChange={() => toggle(capability.id)} /><span>{capability.label}<small>{capability.id}</small></span></label>)}</div></fieldset>)}
  </div>
}
