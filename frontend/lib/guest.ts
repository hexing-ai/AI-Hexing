'use client';
import {createContext, useContext} from 'react';
export const GuestContext=createContext(false);
export function useGuest(){return useContext(GuestContext);}
export const guestReadOnlyMessage='访客模式只能查看，不能修改或生成。';
