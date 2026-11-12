// 三步选题的最近选择（仅用于回到选题页时预填，不承载业务判断）
let last = { bankId: "", specialtyCode: "", paperType: "" };

export const getSelection = () => ({ ...last });

export function saveSelection(patch) {
  last = { ...last, ...patch };
}

export function resetSelection() {
  last = { bankId: "", specialtyCode: "", paperType: "" };
}
