import { call } from "@decky/api";
import type { Result } from "./types";
import type { TfInspection, TfPlan, TfStatus } from "./tf_types";

// The device path is only ever supplied by a previously inspected card, and the
// start call takes the single-use token from the returned plan — never a
// front-end device path.
export const getTfInspection = () => call<[], Result<TfInspection>>("get_tf_inspection");

export const prepareTfLibrary = (device: string) =>
  call<[string], Result<TfPlan>>("prepare_tf_library", device);

export const startTfLibrary = (token: string, confirmed: boolean) =>
  call<[string, boolean], Result<TfStatus>>("start_tf_library", token, confirmed);

export const getTfStatus = () => call<[], Result<TfStatus>>("get_tf_status");
