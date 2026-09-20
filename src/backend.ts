import { call } from "@decky/api";
import type { Inspection, Plan, Result, Status } from "./types";

export const getInspection = () => call<[], Result<Inspection>>("get_inspection");

export const prepareInstall = (androidGib: number | null) =>
  call<[number | null], Result<Plan>>("prepare_install", androidGib);

export const startInstall = (token: string, confirmed: boolean) =>
  call<[string, boolean], Result<Status>>("start_install", token, confirmed);

export const getStatus = () => call<[], Result<Status>>("get_status");

export const powerOff = () => call<[], Result<null>>("power_off");
