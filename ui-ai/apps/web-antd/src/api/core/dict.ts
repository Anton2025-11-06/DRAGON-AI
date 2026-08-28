import { dict } from '@fast-crud/fast-crud';

export enum SysDictCode {
  EDUCATION = 'EDUCATION',
  INDUSTRY = 'INDUSTRY',
  NATION = 'NATION',
  NOTICE = 'NOTICE',
  SEX = 'SEX',
  STATUS = 'STATUS',
}

/**
 * 后端暂未提供字典接口，使用本地静态字典
 * 后续后端补充字典接口后可改为远程 URL
 */
const LOCAL_DICT_MAP: Record<string, { label: string; value: any; color?: string }[]> = {
  [SysDictCode.SEX]: [
    { label: '男', value: '1', color: 'primary' },
    { label: '女', value: '2', color: 'warning' },
    { label: '保密', value: '0', color: 'default' },
  ],
  [SysDictCode.NATION]: [
    { label: '汉族', value: '01' },
    { label: '蒙古族', value: '02' },
    { label: '回族', value: '03' },
    { label: '藏族', value: '04' },
    { label: '维吾尔族', value: '05' },
    { label: '苗族', value: '06' },
    { label: '彝族', value: '07' },
    { label: '壮族', value: '08' },
    { label: '布依族', value: '09' },
    { label: '朝鲜族', value: '10' },
    { label: '满族', value: '11' },
    { label: '侗族', value: '12' },
    { label: '瑶族', value: '13' },
    { label: '白族', value: '14' },
    { label: '土家族', value: '15' },
    { label: '哈尼族', value: '16' },
    { label: '哈萨克族', value: '17' },
    { label: '傣族', value: '18' },
    { label: '黎族', value: '19' },
    { label: '傈僳族', value: '20' },
    { label: '佤族', value: '21' },
    { label: '畲族', value: '22' },
    { label: '高山族', value: '23' },
    { label: '拉祜族', value: '24' },
    { label: '水族', value: '25' },
    { label: '东乡族', value: '26' },
    { label: '纳西族', value: '27' },
    { label: '景颇族', value: '28' },
    { label: '柯尔克孜族', value: '29' },
    { label: '土族', value: '30' },
    { label: '达斡尔族', value: '31' },
    { label: '仫佬族', value: '32' },
    { label: '羌族', value: '33' },
    { label: '布朗族', value: '34' },
    { label: '撒拉族', value: '35' },
    { label: '毛南族', value: '36' },
    { label: '仡佬族', value: '37' },
    { label: '锡伯族', value: '38' },
    { label: '阿昌族', value: '39' },
    { label: '普米族', value: '40' },
    { label: '塔吉克族', value: '41' },
    { label: '怒族', value: '42' },
    { label: '乌孜别克族', value: '43' },
    { label: '俄罗斯族', value: '44' },
    { label: '鄂温克族', value: '45' },
    { label: '德昂族', value: '46' },
    { label: '保安族', value: '47' },
    { label: '裕固族', value: '48' },
    { label: '京族', value: '49' },
    { label: '塔塔尔族', value: '50' },
    { label: '独龙族', value: '51' },
    { label: '鄂伦春族', value: '52' },
    { label: '赫哲族', value: '53' },
    { label: '门巴族', value: '54' },
    { label: '珞巴族', value: '55' },
    { label: '基诺族', value: '56' },
  ],
  [SysDictCode.EDUCATION]: [
    { label: '小学', value: '1' },
    { label: '初中', value: '2' },
    { label: '高中', value: '3' },
    { label: '中专', value: '4' },
    { label: '大专', value: '5' },
    { label: '本科', value: '6' },
    { label: '硕士', value: '7' },
    { label: '博士', value: '8' },
  ],
  [SysDictCode.STATUS]: [
    { label: '启用', value: 1, color: 'success' },
    { label: '停用', value: 0, color: 'error' },
  ],
  [SysDictCode.NOTICE]: [
    { label: '通知', value: '1', color: 'primary' },
    { label: '公告', value: '2', color: 'warning' },
  ],
  [SysDictCode.INDUSTRY]: [
    { label: '信息技术', value: 'IT' },
    { label: '金融', value: 'FINANCE' },
    { label: '制造', value: 'MANUFACTURING' },
    { label: '教育', value: 'EDUCATION' },
    { label: '医疗', value: 'MEDICAL' },
    { label: '其他', value: 'OTHER' },
  ],
};

export const sysDictFunc = (code: SysDictCode) => {
  return dict({
    data: LOCAL_DICT_MAP[code] ?? [],
  });
};

export enum LocalDictCode {
  WF_TASK_STATUS = 'WF_TASK_STATUS',
}

// 创建字典Map
const localDictMap = new Map();
// 初始化字典数据  danger
localDictMap.set(LocalDictCode.WF_TASK_STATUS, [
  { label: '待审批', value: '1', cssClass: '', listClass: 'primary' },
  { label: '审批通过', value: '2', cssClass: '', listClass: 'success' },
  { label: '已终止', value: '4', cssClass: '', listClass: 'danger' },
  { label: '已完成', value: '8', cssClass: '', listClass: 'success' },
]);

export const localDictList = (code: LocalDictCode) => {
  return localDictMap.get(code);
};

/** 字典数据项 */
export interface DictData {
  label: string;
  value: string | number;
  dictValue?: string | number;
  cssClass?: string;
  listClass?: string;
  color?: string;
}

/**
 * 按字典编码查询字典项
 * 后端暂未提供字典接口，暂返回本地静态字典
 * @param dictName 字典编码，如 SEX / STATUS
 */
export function dictDataInfo(dictName: string): Promise<DictData[]> {
  const list = LOCAL_DICT_MAP[dictName as SysDictCode] ?? [];
  return Promise.resolve(
    list.map((item) => ({
      label: item.label,
      value: item.value,
      color: item.color,
    })),
  );
}