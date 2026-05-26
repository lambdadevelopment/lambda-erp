import { useRoutes } from "react-router-dom";
import { buildRoutes } from "./routes";

export default function App() {
  return useRoutes(buildRoutes());
}
