import type SvgIcon from "@mui/material/SvgIcon";
import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import AirIcon from "@mui/icons-material/Air";
import BoltIcon from "@mui/icons-material/Bolt";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import DashboardOutlinedIcon from "@mui/icons-material/DashboardOutlined";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import LoopIcon from "@mui/icons-material/Loop";
import MemoryIcon from "@mui/icons-material/Memory";
import MultilineChartIcon from "@mui/icons-material/MultilineChart";
import NotificationsNoneIcon from "@mui/icons-material/NotificationsNone";
import PlaylistPlayIcon from "@mui/icons-material/PlaylistPlay";
import RadioButtonUncheckedIcon from "@mui/icons-material/RadioButtonUnchecked";
import ScienceOutlinedIcon from "@mui/icons-material/ScienceOutlined";
import SensorsIcon from "@mui/icons-material/Sensors";
import ShowChartIcon from "@mui/icons-material/ShowChart";
import SpaceDashboardOutlinedIcon from "@mui/icons-material/SpaceDashboardOutlined";
import SpeedIcon from "@mui/icons-material/Speed";
import StorageOutlinedIcon from "@mui/icons-material/StorageOutlined";
import ThermostatIcon from "@mui/icons-material/Thermostat";
import TuneIcon from "@mui/icons-material/Tune";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import WaterDropOutlinedIcon from "@mui/icons-material/WaterDropOutlined";
import type { SignalOut } from "@flyball/client";
import type { Page } from "./router.js";

export type IconComponent = typeof SvgIcon;

export const PAGE_ICONS: Record<Page, IconComponent> = {
  overview: SpaceDashboardOutlinedIcon,
  dashboards: DashboardOutlinedIcon,
  inputs: ShowChartIcon,
  graph: MultilineChartIcon,
  controllers: LoopIcon,
  devices: MemoryIcon,
  rig: AccountTreeOutlinedIcon,
  programs: PlaylistPlayIcon,
  events: NotificationsNoneIcon,
  sessions: StorageOutlinedIcon,
  simulation: ScienceOutlinedIcon,
};

/** An icon for a signal from what it measures — the quantity first, then the unit as a fallback. */
export function signalIcon(signal: Pick<SignalOut, "unit" | "quantity">): IconComponent {
  const u = signal.unit.toLowerCase();
  const q = signal.quantity.toLowerCase();
  if (u.includes("rh") || q.includes("humid")) return WaterDropOutlinedIcon;
  if (u.includes("°") || u === "k" || q.includes("temp")) return ThermostatIcon;
  if (u.includes("/min") || u.includes("/s") || q.includes("flow")) return AirIcon;
  if (u === "v" || u === "a" || u === "w" || q === "power") return BoltIcon;
  return SpeedIcon;
}

export {
  CheckCircleOutlineIcon as OkIcon,
  RadioButtonUncheckedIcon as CircleIcon,
  SensorsIcon as SignalIcon,
  TuneIcon as WriteIcon,
  WarningAmberIcon as WarnIcon,
  ErrorOutlineIcon as ErrorIcon,
};
