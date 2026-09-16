import type SvgIcon from "@mui/material/SvgIcon";
import AirIcon from "@mui/icons-material/Air";
import BoltIcon from "@mui/icons-material/Bolt";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import DashboardOutlinedIcon from "@mui/icons-material/DashboardOutlined";
import LoopIcon from "@mui/icons-material/Loop";
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
import type { ChannelOut } from "@flyball/client";
import type { Page } from "./router.js";

export type IconComponent = typeof SvgIcon;

export const PAGE_ICONS: Record<Page, IconComponent> = {
  overview: DashboardOutlinedIcon,
  dashboards: SpaceDashboardOutlinedIcon,
  sources: ShowChartIcon,
  actuators: TuneIcon,
  loops: LoopIcon,
  programs: PlaylistPlayIcon,
  events: NotificationsNoneIcon,
  sessions: StorageOutlinedIcon,
  simulation: ScienceOutlinedIcon,
  readers: SensorsIcon,
};

/** An icon for a channel from what its schema says — the dimension first, then the unit as a fallback. */
export function channelIcon(channel: ChannelOut): IconComponent {
  const u = channel.unit.toLowerCase();
  const m = channel.measurand.toLowerCase();
  if (u.includes("rh") || m.includes("humid")) return WaterDropOutlinedIcon;
  if (u.includes("°") || u === "k" || m.includes("temp")) return ThermostatIcon;
  if (u.includes("/min") || u.includes("/s") || m.includes("flow")) return AirIcon;
  if (u === "v" || u === "a" || u === "w") return BoltIcon;
  return SpeedIcon;
}

export {
  CheckCircleOutlineIcon as OkIcon,
  RadioButtonUncheckedIcon as CircleIcon,
  SensorsIcon as SourceIcon,
  WarningAmberIcon as WarnIcon,
};
