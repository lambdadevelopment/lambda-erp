/**
 * Component override registry.
 *
 * Lets a customer deployment swap a specific UI component for its own without
 * forking the core. Core render sites that want to be overridable resolve
 * their component through `getComponent(name, CoreDefault)` instead of
 * referencing the concrete component directly; the customer calls
 * `registerComponent(name, MyComponent)` at startup.
 *
 * This is the fine-grained seam. For whole pages, prefer the route registry
 * (registerRoute) — it replaces the routed element directly.
 */
import type { ComponentType } from "react";

const COMPONENTS: Record<string, ComponentType<any>> = {};

export function registerComponent(name: string, component: ComponentType<any>) {
  COMPONENTS[name] = component;
}

/** Resolve a registered override, falling back to the core default. */
export function getComponent<P>(
  name: string,
  fallback: ComponentType<P>,
): ComponentType<P> {
  return (COMPONENTS[name] as ComponentType<P>) ?? fallback;
}
